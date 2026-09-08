from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from itertools import combinations
from math import ceil, sqrt

import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize

from portfolio_app.services.annualization import annualization_eligibility
from portfolio_app.services.analytics_scope import (
    resolve_instrument_analytics_scopes,
    taxonomy_configuration_as_of,
    taxonomy_configuration_revisions_through,
)
from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
)
from portfolio_app.services import holdings_market_profile, valuation_fx
from portfolio_app.services.daily_snapshots import list_materialized_daily_snapshots
from portfolio_app.services.instrument_registry import (
    get_shared_fx_rates,
    get_registry_instrument_detail,
    get_registry_instrument_details,
)
from portfolio_app.services.market_data import (
    analytical_return_quote_bases,
    benchmark_total_return_quote_bases,
    resolve_quote_series,
)
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.performance import build_holdings_report
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_accounts,
    list_portfolio_instrument_universe,
    list_transactions,
)
from portfolio_app.services.research_eligibility import (
    contract_only_instrument_ids,
    FORMER_PM_REVIEW_EXECUTION_NOTE,
    RESEARCH_EXECUTION_TARGET_EPSILON,
    enrich_instrument_research_state,
)
from portfolio_app.services.transaction_dates import transaction_performance_effective_date

ROOT_SCOPE_MEMBER_ID = "__portfolio_root__"
ROOT_SCOPE_LABEL = "Top Level"
TARGET_DIMENSION_SCOPE_DEFAULT = "scope_default"
TARGET_DIMENSION_WEIGHT = "weight"
TARGET_DIMENSION_RISK_BUDGET = "risk_budget"
TARGET_MEMBER_NODE = "taxonomy_node"
TARGET_MEMBER_INSTRUMENT = "instrument"
TARGET_MEMBER_CASH = "cash_bucket"
TARGET_MEMBER_DERIVATIVE = "derivative_bucket"
SYSTEM_CASH_TARGET_MEMBER_ID = "__cash__"
SYSTEM_CASH_TARGET_LABEL = "Cash"
SYSTEM_DERIVATIVE_TARGET_MEMBER_ID = "__derivatives__"
SYSTEM_DERIVATIVE_TARGET_LABEL = "Derivatives"
CAPITAL_MODE_UNIT_NOTIONAL = "unit_notional"
CAPITAL_MODE_FIXED_GROSS = "fixed_gross"
CAPITAL_MODE_TARGET_VOLATILITY = "target_volatility"
CAPITAL_MODE_VOLATILITY_CAP = "volatility_cap"
RESEARCH_COVARIANCE_MODEL_ID = "ewma_vol_shrinkage_corr_covariance"
RESEARCH_COVARIANCE_FREQUENCY_PARAMETERS: dict[CalculationFrequency, dict[str, object]] = {
    "daily": {
        "min_observations": 45,
        "vol_decay": 0.9945,
        "corr_min_observations": 45,
        "corr_shrinkage": 0.15,
        "max_period_staleness_days": 5,
    },
}
RESEARCH_MIN_OBSERVATION_COVERAGE_RATIO = 0.75
RESEARCH_WINDOW_MONTHS_BY_LOOKBACK_DAYS = {
    30: 1.0,
    90: 3.0,
    180: 6.0,
    366: 12.0,
    730: 24.0,
}
RESEARCH_OBSERVATIONS_PER_MONTH_BY_FREQUENCY: dict[CalculationFrequency, float] = {
    "daily": 20.0,
}
RESEARCH_RISK_CONTRIBUTION_MODE = "signed"
RESEARCH_MAX_RISK_BUDGET_SHARE_GAP = 1e-4
RESEARCH_COVARIANCE_PSD_TOLERANCE = 1e-10
RISK_BUDGET_NORMALIZED_GAP_FLOOR_EQUAL_SHARE_FRACTION = 0.25
RISK_BUDGET_NORMALIZED_GAP_MAX_FLOOR = 0.05
MISSING_RETURN_POLICY_STRICT = "strict"
MISSING_RETURN_POLICY_COMPLETE_CASE_DROP = "complete_case_drop"
RESEARCH_DEFAULT_MISSING_RETURN_POLICY = MISSING_RETURN_POLICY_STRICT
RESEARCH_COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION = 0.10
RESEARCH_COMPLETE_CASE_DROP_MAX_TRAILING_STALENESS_DAYS: dict[CalculationFrequency, int] = {
    "daily": 5,
}
SUPPORTED_RESEARCH_LOOKBACK_DAYS = frozenset(RESEARCH_WINDOW_MONTHS_BY_LOOKBACK_DAYS)
RESEARCH_BACKTEST_METHODOLOGY_WARNINGS: tuple[str, ...] = (
    "Each rebalance uses the taxonomy membership and target policy revision effective on its decision date.",
    "An instrument becomes usable only after its effective assignment and first usable market-data observation.",
    "Simulation results include the configured cash yield, commission, sell-side tax, slippage, and implementation delay assumptions.",
    "Market observations are EOD period-end returns: holdings earn the return ending before an EOD execution, and newly executed targets start with the next observation.",
    "A scheduled execution is skipped rather than valued from stale NAV when any pre-trade holding lacks a complete EOD return observation; point-in-time coverage is marked partial.",
    "FCN/options are no-trade positions outside covariance and Risk Budget. Their recorded carrying capital changes only on actual derivative lifecycle dates and earns no cash yield; coupon, payoff, issuer loss, FX risk, collateral, and lifecycle liquidity are not modeled. This is not a full-portfolio fair-value backtest.",
    "Current no-trade taxonomy sleeves are not applied retroactively because the settings have no effective-dated restriction history; the historical simulation uses the effective taxonomy/targets without today's manual freeze state.",
    "Taxonomy and targets are effective-dated; the selected risk-model and run settings are held fixed across the simulation. EOD source dates do not prove historical publication-time availability, especially for delayed fund NAVs.",
)


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
    top_sleeve_bound_weight_by_id: dict[str, float]


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
    target_status: str = "satisfied"
    execution_ready: bool = True


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
    missing_return_policy: str | None = None
    return_rows_before_policy: int | None = None
    return_rows_after_policy: int | None = None
    missing_return_row_count: int | None = None
    missing_return_row_fraction: float | None = None
    leading_incomplete_return_row_count: int | None = None
    post_warmup_missing_return_row_count: int | None = None
    post_warmup_missing_return_row_fraction: float | None = None
    dropped_return_rows: list[dict[str, object]] | None = None
    latest_complete_return_date: str | None = None
    trailing_complete_return_staleness_days: int | None = None
    target_status: str = "satisfied"
    execution_ready: bool = True


@dataclass(frozen=True)
class ReturnCoveragePolicyResult:
    returns: pd.DataFrame
    policy: str
    rows_before: int
    rows_after: int
    missing_row_count: int
    missing_row_fraction: float
    leading_incomplete_row_count: int
    post_warmup_missing_row_count: int
    post_warmup_missing_row_fraction: float
    dropped_rows: list[dict[str, object]]
    latest_complete_date: date | None
    trailing_staleness_days: int | None


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
    top_sleeve_weight_bounds: dict[str, dict[str, float | None]]
    configuration_version: int | None = None
    configuration_effective_from: date | None = None
    instrument_analytics_scopes: dict[str, dict[str, object]] = field(default_factory=dict)
    # A current-target solve walks the taxonomy recursively.  Current holdings
    # and account values are portfolio-level inputs, so rebuilding both ledgers
    # once per scope is redundant and can make deep taxonomies disproportionately
    # expensive.  Keep the lazy valuation snapshot on the per-solve state; a new
    # state is constructed for every as-of date (including each backtest
    # rebalance), so this cache never crosses a point-in-time boundary.
    current_valuation_cache: dict[str, object] = field(default_factory=dict, repr=False, compare=False)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _research_window_months(lookback_days: int) -> int:
    try:
        months = RESEARCH_WINDOW_MONTHS_BY_LOOKBACK_DAYS[int(lookback_days)]
    except (KeyError, TypeError, ValueError) as error:
        labels = ", ".join(f"{int(months)}M" for months in RESEARCH_WINDOW_MONTHS_BY_LOOKBACK_DAYS.values())
        raise ValueError(f"Risk window must be one of {labels}.") from error
    return int(months)


def research_window_start_date(end_date: date, lookback_days: int) -> date:
    months = _research_window_months(lookback_days)
    return (pd.Timestamp(end_date) - pd.DateOffset(months=months)).date()


def _normalize_top_sleeve_weight_bounds(
    bounds: list[dict[str, object]] | None,
) -> dict[str, dict[str, float | None]]:
    normalized: dict[str, dict[str, float | None]] = {}
    for item in bounds or []:
        node_id = str((item or {}).get("taxonomy_node_id") or "").strip()
        if not node_id:
            continue
        min_weight = _safe_float((item or {}).get("min_weight"))
        max_weight = _safe_float((item or {}).get("max_weight"))
        if min_weight is None and max_weight is None:
            continue
        if min_weight is not None and not 0.0 <= min_weight <= 1.0:
            raise ValueError("Top sleeve min_weight must be between 0 and 1.")
        if max_weight is not None and not 0.0 <= max_weight <= 1.0:
            raise ValueError("Top sleeve max_weight must be between 0 and 1.")
        if min_weight is not None and max_weight is not None and min_weight > max_weight:
            raise ValueError("Top sleeve min_weight cannot exceed max_weight.")
        normalized[node_id] = {"min_weight": min_weight, "max_weight": max_weight}
    return normalized


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


def _instrument_detail_from_cache(
    instrument_detail_cache: dict[str, dict[str, object] | None],
    instrument_id: str,
) -> dict[str, object] | None:
    if instrument_id not in instrument_detail_cache:
        instrument_detail_cache[instrument_id] = get_registry_instrument_detail(
            instrument_id
        )
    return instrument_detail_cache[instrument_id]


def _instrument_detail(
    state: TaxonomyResearchState,
    instrument_id: str,
) -> dict[str, object] | None:
    return _instrument_detail_from_cache(
        state.instrument_detail_cache,
        instrument_id,
    )


def _candidate_quote_bases(detail: dict[str, object]) -> list[str]:
    return analytical_return_quote_bases(detail)


def _selected_price_points(
    detail: dict[str, object],
    *,
    end_date: date,
    candidate_bases: list[str] | None = None,
) -> list[tuple[date, float, str]]:
    resolution = resolve_quote_series(
        detail,
        candidate_bases=(
            candidate_bases
            if candidate_bases is not None
            else _candidate_quote_bases(detail)
        ),
        end_date=end_date,
    )
    if not resolution.available:
        return []
    selected: list[tuple[date, float, str]] = []
    for point in resolution.points:
        point_date = point.get("as_of_date")
        point_value = _safe_float(point.get("value"))
        if not isinstance(point_date, date) or point_value is None:
            return []
        selected.append((point_date, point_value, str(point.get("currency") or "")))
    return selected


def _convert_price_to_base(
    state: TaxonomyResearchState,
    *,
    point_date: date,
    value: float,
    point_currency: str,
) -> float | None:
    normalized_currency = valuation_fx.required_currency(
        point_currency,
        field_name="market-data currency",
    )
    if normalized_currency == state.base_currency:
        return value
    fx = valuation_fx.resolve_fx_rate_on(
        as_of_date=point_date,
        base_currency=normalized_currency,
        quote_currency=state.base_currency,
        direct_instruments=state.direct_fx_instruments,
        instrument_detail_cache=state.instrument_detail_cache,
        instrument_detail_loader=get_registry_instrument_detail,
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
    warn_on_start_clip: bool = True,
    candidate_bases: list[str] | None = None,
) -> tuple[pd.Series, list[str]]:
    detail = _instrument_detail(state, instrument_id)
    if not isinstance(detail, dict):
        raise ValueError(f"Instrument detail for {instrument_id} is unavailable.")

    selected_points = _selected_price_points(
        detail,
        end_date=end_date,
        candidate_bases=candidate_bases,
    )
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
    anchor = series.loc[series.index <= start_date].tail(1)
    visible = pd.concat([anchor, series.loc[(series.index > start_date) & (series.index <= end_date)]])
    if visible.empty:
        visible = series.loc[series.index <= end_date]
    if visible.empty:
        raise ValueError(f"{instrument_id} does not have any observations on or before the selected end date.")
    if warn_on_start_clip and visible.index[0] > start_date:
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


def _node_has_research_members(
    state: TaxonomyResearchState,
    node_id: str,
) -> bool:
    return any(
        state.direct_assignments_by_node.get(subtree_node_id)
        for subtree_node_id in state.node_subtree_by_id.get(node_id, {node_id})
    )


def _member_is_fixed_capital(member: ScopeMemberRecord) -> bool:
    return member.member_type in {TARGET_MEMBER_CASH, TARGET_MEMBER_DERIVATIVE}


def _scope_is_frozen(state: TaxonomyResearchState, scope_node_id: str | None) -> bool:
    if scope_node_id is None:
        return False
    return scope_node_id in state.frozen_taxonomy_node_ids


def _member_is_frozen(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    member: ScopeMemberRecord,
    inherited_no_trade: bool = False,
) -> bool:
    if _member_is_fixed_capital(member):
        return False
    if inherited_no_trade or _scope_is_frozen(state, scope_node_id):
        return True
    return member.member_type == TARGET_MEMBER_NODE and member.member_id in state.frozen_taxonomy_node_ids


def _member_has_no_trade_constraint(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    member: ScopeMemberRecord,
    inherited_no_trade: bool = False,
) -> bool:
    return member.member_type == TARGET_MEMBER_DERIVATIVE or _member_is_frozen(
        state,
        scope_node_id=scope_node_id,
        member=member,
        inherited_no_trade=inherited_no_trade,
    )


def _member_risk_model_status(member: ScopeMemberRecord) -> str:
    return (
        "excluded"
        if member.member_type in {TARGET_MEMBER_CASH, TARGET_MEMBER_DERIVATIVE}
        else "modeled"
    )


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


def _allocate_fixed_capital_weights(
    *,
    member_index: list[str],
    preferred_weights: pd.Series,
    total_weight: float,
) -> pd.Series:
    if not member_index:
        return pd.Series(dtype="float64")
    resolved_total = float(total_weight)
    preferred = preferred_weights.reindex(member_index, fill_value=0.0).astype("float64")
    if not np.isfinite(preferred.to_numpy(dtype="float64")).all() or not np.isfinite(resolved_total):
        raise ValueError("Fixed-capital allocation requires finite weights.")
    cash_key = f"{TARGET_MEMBER_CASH}::{SYSTEM_CASH_TARGET_MEMBER_ID}"
    derivative_key = f"{TARGET_MEMBER_DERIVATIVE}::{SYSTEM_DERIVATIVE_TARGET_MEMBER_ID}"
    invalid_negative_keys = [
        key
        for key, value in preferred.items()
        if key != derivative_key and float(value) < -1e-12
    ]
    if invalid_negative_keys:
        raise ValueError("Configured cash and fixed-capital target weights cannot be negative.")
    preferred_total = float(preferred.sum())
    if cash_key not in member_index:
        if abs(resolved_total - preferred_total) <= 1e-12:
            return preferred
        raise ValueError("Fixed-capital residual requires the system cash member.")

    # The derivative weight supplied here is the signed carrying capital fixed
    # by the actual position (or by its point-in-time lifecycle replay), never
    # a tradable policy target.  A volatility or gross-exposure overlay may
    # resize risky securities, but it must not create, resize, or erase FCN /
    # option capital.  All surplus capital and financing therefore flow
    # through the system cash member.
    allocated = preferred.copy()
    allocated.loc[cash_key] = float(allocated.loc[cash_key]) + resolved_total - preferred_total
    return allocated


def _research_covariance_parameters(calculation_frequency: CalculationFrequency) -> dict[str, object]:
    try:
        return dict(RESEARCH_COVARIANCE_FREQUENCY_PARAMETERS[calculation_frequency])
    except KeyError as error:
        raise ValueError(f"Unsupported calculation frequency: {calculation_frequency}.") from error


def research_covariance_parameters(calculation_frequency: CalculationFrequency) -> dict[str, object]:
    return _research_covariance_parameters(calculation_frequency)


def research_min_observations_for_window(
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> int:
    months = _research_window_months(lookback_days)
    observations_per_month = RESEARCH_OBSERVATIONS_PER_MONTH_BY_FREQUENCY[calculation_frequency]
    expected_observations = months * observations_per_month
    return max(2, int(ceil(expected_observations * RESEARCH_MIN_OBSERVATION_COVERAGE_RATIO)))


def research_covariance_parameters_for_window(
    calculation_frequency: CalculationFrequency,
    lookback_days: int,
) -> dict[str, object]:
    parameters = _research_covariance_parameters(calculation_frequency)
    min_observations = research_min_observations_for_window(calculation_frequency, lookback_days)
    parameters["min_observations"] = min_observations
    parameters["corr_min_observations"] = min_observations
    return parameters


def _risk_model_covariance_model_id(risk_model_config: dict[str, object] | None) -> str:
    if not isinstance(risk_model_config, dict):
        return RESEARCH_COVARIANCE_MODEL_ID
    return str(risk_model_config.get("covariance_model_id") or RESEARCH_COVARIANCE_MODEL_ID)


def _risk_model_contribution_mode(risk_model_config: dict[str, object] | None) -> str:
    if not isinstance(risk_model_config, dict):
        return RESEARCH_RISK_CONTRIBUTION_MODE
    return str(risk_model_config.get("contribution_mode") or RESEARCH_RISK_CONTRIBUTION_MODE)


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
    return research_covariance_parameters_for_window(calculation_frequency, lookback_days)


def _normalize_missing_return_policy(value: object) -> str:
    normalized = str(value or RESEARCH_DEFAULT_MISSING_RETURN_POLICY).strip().lower()
    if normalized in {MISSING_RETURN_POLICY_STRICT, MISSING_RETURN_POLICY_COMPLETE_CASE_DROP}:
        return normalized
    raise ValueError(f"Unsupported missing-return policy: {value}.")


def normalize_missing_return_policy(value: object) -> str:
    return _normalize_missing_return_policy(value)


def _max_complete_case_drop_staleness_days(calculation_frequency: CalculationFrequency) -> int:
    try:
        return RESEARCH_COMPLETE_CASE_DROP_MAX_TRAILING_STALENESS_DAYS[calculation_frequency]
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
            f"missing return values were found for period ends {missing_dates}. "
            "A NAV or price at a period end is not a return unless the aligned "
            "period start is also available."
        )
    if len(returns) < required:
        raise ValueError(
            f"{label} requires at least {required} complete aligned return observations; got {len(returns)}."
        )


def _return_window_for_lookback(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    as_of_date: date | None = None,
) -> pd.DataFrame:
    cleaned = _clean_return_frame(returns)
    if cleaned.empty:
        raise ValueError("Covariance estimation requires non-empty returns.")
    end_day = as_of_date or pd.Timestamp(max(cleaned.index)).date()
    start_day = research_window_start_date(end_day, lookback_days)
    # ``start_day`` and ``end_day`` are EOD valuation boundaries. Return rows
    # are labelled by their period end, so a close-to-close window links
    # observations in (start_day, end_day]. A return ending on start_day belongs
    # to the preceding interval and must not leak into this window.
    window = cleaned.loc[(cleaned.index > start_day) & (cleaned.index <= end_day)].copy()
    if window.empty:
        raise ValueError(
            f"Covariance estimation has no return observations in the requested "
            f"({start_day.isoformat()}, {end_day.isoformat()}] EOD window."
        )
    return window


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
    max_trailing_staleness_days: int | None = None,
) -> ReturnCoveragePolicyResult:
    policy = _normalize_missing_return_policy(missing_return_policy)
    required = max(int(min_observations), 2)
    if returns.empty:
        raise ValueError(f"{label} requires non-empty aligned returns.")

    missing_mask = returns.isna().any(axis=1)
    missing_count = int(missing_mask.sum())
    missing_fraction = float(missing_count / max(len(returns), 1))
    dropped_rows = _render_missing_return_rows(returns)
    complete_mask = ~missing_mask
    if bool(complete_mask.any()):
        first_complete_position = int(np.flatnonzero(complete_mask.to_numpy(dtype=bool))[0])
    else:
        first_complete_position = len(returns)
    leading_incomplete_count = int(missing_mask.iloc[:first_complete_position].sum())
    post_warmup_mask = missing_mask.iloc[first_complete_position:]
    post_warmup_missing_count = int(post_warmup_mask.sum())
    post_warmup_missing_fraction = float(
        post_warmup_missing_count / max(len(post_warmup_mask), 1)
    )

    if policy == MISSING_RETURN_POLICY_STRICT:
        _validate_complete_return_coverage(
            returns,
            min_observations=min_observations,
            label=label,
        )
        complete = returns.copy()
    else:
        complete = returns.loc[~missing_mask].copy()
        # A contiguous incomplete prefix is the causal common-history boundary,
        # not an intermittent data-quality gap. Complete-case mode may begin at
        # the first aligned return when the remaining common sample is large
        # enough. Missing rows after that boundary still face the quality limit,
        # and trailing freshness is checked below.
        if (
            post_warmup_missing_count
            and post_warmup_missing_fraction > RESEARCH_COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION + 1e-12
        ):
            raise ValueError(
                f"{label} complete-case drop would remove {post_warmup_missing_count} of "
                f"{len(post_warmup_mask)} post-warmup return rows "
                f"({post_warmup_missing_fraction:.2%}), exceeding the "
                f"{RESEARCH_COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION:.2%} limit. "
                f"Missing return period ends: "
                f"{_format_index_sample(post_warmup_mask.loc[post_warmup_mask].index)}. "
                "A NAV or price on a period end alone is insufficient without "
                "an aligned period start."
            )
        if len(complete) < required:
            raise ValueError(
                f"{label} complete-case drop requires at least {required} complete aligned return observations; "
                f"got {len(complete)} after dropping {missing_count} rows."
            )
    if as_of_date is not None and len(complete):
        latest_date = pd.Timestamp(max(complete.index)).date()
        staleness_days = int((as_of_date - latest_date).days)
        max_staleness_days = (
            _max_complete_case_drop_staleness_days(calculation_frequency)
            if max_trailing_staleness_days is None
            else int(max_trailing_staleness_days)
        )
        if max_staleness_days < 0:
            raise ValueError("Maximum trailing return staleness cannot be negative.")
        if staleness_days > max_staleness_days:
            policy_label = "complete-case drop" if policy == MISSING_RETURN_POLICY_COMPLETE_CASE_DROP else "strict"
            raise ValueError(
                f"{label} {policy_label} latest complete return observation is "
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
        leading_incomplete_row_count=leading_incomplete_count,
        post_warmup_missing_row_count=post_warmup_missing_count,
        post_warmup_missing_row_fraction=post_warmup_missing_fraction,
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
    max_trailing_staleness_days: int | None = None,
) -> ReturnCoveragePolicyResult:
    window = _return_window_for_lookback(
        returns,
        lookback_days=lookback_days,
        as_of_date=as_of_date,
    )
    return _apply_missing_return_policy(
        window,
        min_observations=min_observations,
        label=label,
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
        max_trailing_staleness_days=max_trailing_staleness_days,
    )


def prepare_return_window_for_covariance(
    returns: pd.DataFrame,
    *,
    lookback_days: int,
    min_observations: int,
    label: str,
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    calculation_frequency: CalculationFrequency = "daily",
    as_of_date: date | None = None,
    max_trailing_staleness_days: int | None = None,
) -> ReturnCoveragePolicyResult:
    return _prepare_return_window_for_covariance(
        returns,
        lookback_days=lookback_days,
        min_observations=min_observations,
        label=label,
        missing_return_policy=missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
        max_trailing_staleness_days=max_trailing_staleness_days,
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
        if min_eigenvalue < -RESEARCH_COVARIANCE_PSD_TOLERANCE * scale:
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
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
        max_trailing_staleness_days=int(
            parameters.get(
                "max_period_staleness_days",
                _max_complete_case_drop_staleness_days(calculation_frequency),
            )
        ),
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
        max_trailing_staleness_days=int(
            parameters.get(
                "max_period_staleness_days",
                _max_complete_case_drop_staleness_days(calculation_frequency),
            )
        ),
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
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
            max_trailing_staleness_days=int(
                parameters.get(
                    "max_period_staleness_days",
                    _max_complete_case_drop_staleness_days(calculation_frequency),
                )
            ),
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
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
    vector = np.asarray(values, dtype="float64")
    if not np.isfinite(vector).all():
        raise ValueError("Target vector must contain only finite values.")
    vector = np.clip(vector, 0.0, None)
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
    if len(set(problem.bucket_ids)) != count:
        raise ValueError("Risk budget bucket identifiers must be unique.")
    arrays = {
        "covariance": problem.covariance,
        "target shares": problem.target_risk_shares,
        "lower bounds": problem.lower_bounds,
        "upper bounds": problem.upper_bounds,
        "reference weights": problem.reference_weights,
    }
    for label, values in arrays.items():
        if not np.isfinite(values).all():
            raise ValueError(f"Risk budget {label} must contain only finite values.")
    if not np.allclose(problem.covariance, problem.covariance.T, rtol=1e-10, atol=1e-12):
        raise ValueError("Risk budget covariance must be symmetric.")
    eigenvalues = np.linalg.eigvalsh(0.5 * (problem.covariance + problem.covariance.T))
    covariance_scale = max(float(np.max(np.abs(eigenvalues))), 1.0)
    if float(eigenvalues.min()) < -RESEARCH_COVARIANCE_PSD_TOLERANCE * covariance_scale:
        raise ValueError("Risk budget covariance must be positive semidefinite.")
    if np.any(problem.target_risk_shares < -1e-12):
        raise ValueError("Risk budget target shares cannot be negative.")
    if np.any(problem.lower_bounds < -1e-12) or np.any(problem.upper_bounds > 1.0 + 1e-12):
        raise ValueError("Risk budget bounds must be between 0 and 1.")
    if np.any(problem.upper_bounds < problem.lower_bounds):
        raise ValueError("Risk budget upper bounds cannot be smaller than lower bounds.")
    if float(problem.lower_bounds.sum()) > 1.0 + 1e-10:
        raise ValueError("Risk budget lower bounds are infeasible: their sum exceeds 1.")
    if float(problem.upper_bounds.sum()) < 1.0 - 1e-10:
        raise ValueError("Risk budget upper bounds are infeasible: their sum is below 1.")
    if abs(float(problem.target_risk_shares.sum()) - 1.0) > 1e-8:
        raise ValueError("Risk budget target shares must sum to 1.")


def _solve_convex_risk_budget(
    problem: RiskBudgetProblem,
    *,
    max_iterations: int,
) -> RiskBudgetSolution | None:
    """Solve the canonical unconstrained risk-budget problem.

    For positive budgets and a PSD covariance matrix, minimizing
    ``0.5 * y'Σy - Σ b_i log(y_i)`` over positive ``y`` is convex. Its
    first-order condition gives ``y_i(Σy)_i=b_i``; normalizing ``y`` then
    produces positive signed risk contributions in the requested proportions.
    The same solution therefore also satisfies absolute-contribution budgets,
    while avoiding their non-convex extra branches. Box/frozen constraints use
    the explicit minimax formulation below because their best feasible target
    miss must remain observable.
    """

    if problem.contribution_mode not in {"signed", "abs"} or _risk_budget_problem_has_binding_bounds(problem):
        return None
    if np.any(problem.target_risk_shares <= 0.0):
        return None

    diagonal_volatility = np.sqrt(np.maximum(np.diag(problem.covariance), 1e-12))
    initial = problem.target_risk_shares / diagonal_volatility
    initial_variance = float(initial @ problem.covariance @ initial)
    if initial_variance > 1e-12:
        initial = initial / sqrt(initial_variance)
    initial = np.maximum(initial, 1e-10)

    def objective(scale_weights: np.ndarray) -> float:
        return float(
            0.5 * scale_weights @ problem.covariance @ scale_weights
            - problem.target_risk_shares @ np.log(scale_weights)
        )

    def gradient(scale_weights: np.ndarray) -> np.ndarray:
        return problem.covariance @ scale_weights - problem.target_risk_shares / scale_weights

    result = minimize(
        objective,
        x0=initial,
        jac=gradient,
        method="L-BFGS-B",
        bounds=[(1e-12, None)] * len(problem.bucket_ids),
        options={"ftol": 1e-15, "gtol": 1e-10, "maxiter": max(max_iterations, 500)},
    )
    if (
        not np.isfinite(result.x).all()
        or not np.isfinite(float(result.fun))
        or np.any(np.asarray(result.x, dtype="float64") <= 0.0)
    ):
        return None
    weights = np.asarray(result.x, dtype="float64")
    weights = weights / float(weights.sum())
    shares = _risk_contribution_shares(
        problem.covariance,
        weights,
        contribution_mode=problem.contribution_mode,
    )
    gap = shares - problem.target_risk_shares
    return RiskBudgetSolution(
        bucket_ids=problem.bucket_ids,
        weights=weights,
        achieved_risk_shares=shares,
        objective_value=_normalized_share_gap_l2(problem, shares),
        max_abs_share_gap=float(np.max(np.abs(gap))),
        iterations=int(getattr(result, "nit", 0)),
        message=(
            f"{result.message} (convex log-barrier risk-budget solve"
            + (
                ")"
                if result.success
                else "; non-success termination candidate remains subject to independent risk-budget checks)"
            )
        ),
        solver_kind="convex_log_barrier",
        contribution_mode=problem.contribution_mode,
    )


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
    candidates.extend(_build_risk_budget_boundary_seed_candidates(problem, reference_weights))
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


def _allocate_bounded_mass(
    *,
    total_mass: float,
    lower: np.ndarray,
    upper: np.ndarray,
    preferred: np.ndarray,
) -> np.ndarray:
    weights = np.asarray(lower, dtype="float64").copy()
    lower_total = float(weights.sum())
    upper_total = float(np.asarray(upper, dtype="float64").sum())
    if total_mass < lower_total - 1e-10 or total_mass > upper_total + 1e-10:
        raise ValueError("Risk budget fixed-bound seed is infeasible.")
    remaining = float(total_mass - lower_total)
    free_indices = list(range(len(weights)))
    preferred = np.clip(np.asarray(preferred, dtype="float64"), 0.0, None)
    while remaining > 1e-12 and free_indices:
        active_indices = np.asarray(free_indices, dtype=int)
        active_preferred = preferred[active_indices]
        preferred_total = float(active_preferred.sum())
        if preferred_total <= 1e-12:
            active_preferred = np.ones(len(active_indices), dtype="float64")
            preferred_total = float(active_preferred.sum())
        proposal = weights[active_indices] + remaining * active_preferred / preferred_total
        active_upper = np.asarray(upper, dtype="float64")[active_indices]
        over_mask = proposal > active_upper + 1e-12
        if not np.any(over_mask):
            weights[active_indices] = proposal
            remaining = float(total_mass - float(weights.sum()))
            break
        for index in active_indices[over_mask]:
            weights[index] = float(np.asarray(upper, dtype="float64")[index])
            free_indices.remove(int(index))
        remaining = float(total_mass - float(weights.sum()))
    if abs(float(weights.sum()) - total_mass) > 1e-8:
        raise ValueError("Risk budget fixed-bound seed failed to allocate remaining mass.")
    return weights


def _build_fixed_upper_seed(
    problem: RiskBudgetProblem,
    reference_weights: np.ndarray,
    active_indices: list[int],
) -> np.ndarray | None:
    weights = np.asarray(problem.lower_bounds, dtype="float64").copy()
    active_mask = np.zeros(len(weights), dtype=bool)
    active_mask[active_indices] = True
    weights[active_mask] = problem.upper_bounds[active_mask]
    remaining_mass = float(1.0 - float(weights.sum()))
    if remaining_mass < -1e-10:
        return None
    free_mask = ~active_mask
    if not np.any(free_mask):
        return weights if abs(remaining_mass) <= 1e-8 else None
    try:
        weights[free_mask] = _allocate_bounded_mass(
            total_mass=remaining_mass,
            lower=problem.lower_bounds[free_mask],
            upper=problem.upper_bounds[free_mask],
            preferred=reference_weights[free_mask],
        )
    except ValueError:
        return None
    return weights


def _build_risk_budget_boundary_seed_candidates(
    problem: RiskBudgetProblem,
    reference_weights: np.ndarray,
) -> list[np.ndarray]:
    bounded_indices = [
        index
        for index, (lower, upper) in enumerate(zip(problem.lower_bounds, problem.upper_bounds, strict=True))
        if lower > 1e-12 or upper < 1.0 - 1e-12
    ]
    if not bounded_indices:
        return []
    candidates: list[np.ndarray] = []
    for size in (1, 2):
        for active_indices in combinations(bounded_indices, size):
            candidate = _build_fixed_upper_seed(problem, reference_weights, list(active_indices))
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _risk_budget_problem_has_binding_bounds(problem: RiskBudgetProblem) -> bool:
    return bool(np.any(problem.lower_bounds > 1e-12) or np.any(problem.upper_bounds < 1.0 - 1e-12))


def _risk_share_gap_scales(target_risk_shares: np.ndarray) -> np.ndarray:
    count = max(len(target_risk_shares), 1)
    floor = min(
        RISK_BUDGET_NORMALIZED_GAP_MAX_FLOOR,
        RISK_BUDGET_NORMALIZED_GAP_FLOOR_EQUAL_SHARE_FRACTION / float(count),
    )
    return np.maximum(np.asarray(target_risk_shares, dtype="float64"), floor)


def _normalized_risk_share_gap(problem: RiskBudgetProblem, achieved_risk_shares: np.ndarray) -> np.ndarray:
    gap = np.asarray(achieved_risk_shares, dtype="float64") - problem.target_risk_shares
    return gap / _risk_share_gap_scales(problem.target_risk_shares)


def _solution_max_normalized_share_gap(problem: RiskBudgetProblem, solution: RiskBudgetSolution) -> float:
    normalized_gap = _normalized_risk_share_gap(problem, solution.achieved_risk_shares)
    return float(np.max(np.abs(normalized_gap)))


def _normalized_share_gap_l2(problem: RiskBudgetProblem, achieved_risk_shares: np.ndarray) -> float:
    normalized_gap = _normalized_risk_share_gap(problem, achieved_risk_shares)
    return float(normalized_gap @ normalized_gap)


def _is_better_risk_budget_solution(
    problem: RiskBudgetProblem,
    candidate: RiskBudgetSolution,
    incumbent: RiskBudgetSolution,
) -> bool:
    candidate_normalized_gap = _solution_max_normalized_share_gap(problem, candidate)
    incumbent_normalized_gap = _solution_max_normalized_share_gap(problem, incumbent)
    if candidate_normalized_gap < incumbent_normalized_gap - 1e-9:
        return True
    if abs(candidate_normalized_gap - incumbent_normalized_gap) > 1e-9:
        return False
    candidate_l2 = _normalized_share_gap_l2(problem, candidate.achieved_risk_shares)
    incumbent_l2 = _normalized_share_gap_l2(problem, incumbent.achieved_risk_shares)
    if candidate_l2 < incumbent_l2 - 1e-12:
        return True
    if abs(candidate_l2 - incumbent_l2) > 1e-12:
        return False
    return candidate.max_abs_share_gap < incumbent.max_abs_share_gap - 1e-12


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
        normalized_gap = _normalized_risk_share_gap(problem, shares)
        reference_gap = weights - reference_weights
        return (
            float(np.max(np.abs(normalized_gap)) ** 2)
            + 1e-2 * float(normalized_gap @ normalized_gap)
            + 1e-4 * float(reference_gap @ reference_gap)
        )

    constraints = [{"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)}]
    bounds = list(
        zip(
            problem.lower_bounds.tolist(),
            problem.upper_bounds.tolist(),
            strict=True,
        )
    )
    best_solution: tuple[np.ndarray, np.ndarray, float, float, int, str] | None = None
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
        normalized_gap = _normalized_risk_share_gap(problem, shares)
        candidate = (
            weights,
            shares,
            float(np.max(np.abs(share_gap))),
            float(np.max(np.abs(normalized_gap))),
            int(getattr(result, "nit", 0)),
            str(result.message),
        )
        if best_solution is None or candidate[3] < best_solution[3] - 1e-12:
            best_solution = candidate
        elif best_solution is not None and abs(candidate[3] - best_solution[3]) <= 1e-12:
            if objective(candidate[0]) < objective(best_solution[0]) - 1e-18:
                best_solution = candidate

    if best_solution is None:
        detail = "" if not failures else f": {'; '.join(sorted(set(failures)))}"
        raise ValueError(f"Risk budget solver failed{detail}")

    weights, shares, max_abs_share_gap, _max_normalized_share_gap, iterations, message = best_solution
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
    def achieved_shares(weights: np.ndarray) -> np.ndarray:
        return _risk_contribution_shares(
            problem.covariance,
            weights,
            contribution_mode=problem.contribution_mode,
        )

    def normalized_share_gap(weights: np.ndarray) -> np.ndarray:
        return _normalized_risk_share_gap(problem, achieved_shares(weights))

    constraints = [{"type": "eq", "fun": lambda variables: float(np.sum(variables[:-1]) - 1.0)}]
    for index in range(len(problem.bucket_ids)):
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda variables, index=index: float(
                    variables[-1] - normalized_share_gap(variables[:-1])[index]
                ),
            }
        )
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda variables, index=index: float(
                    variables[-1] + normalized_share_gap(variables[:-1])[index]
                ),
            }
        )

    bounds = list(
        zip(
            problem.lower_bounds.tolist(),
            problem.upper_bounds.tolist(),
            strict=True,
        )
    ) + [(0.0, None)]
    best_solution: tuple[np.ndarray, np.ndarray, float, float, float, int, str] | None = None
    for guess in initial_guesses:
        initial_gap = float(np.max(np.abs(normalized_share_gap(guess))))
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
        shares = achieved_shares(weights)
        gap = shares - problem.target_risk_shares
        normalized_gap = _normalized_risk_share_gap(problem, shares)
        reference_gap = weights - reference_weights
        secondary_score = float(normalized_gap @ normalized_gap) + 1e-4 * float(reference_gap @ reference_gap)
        candidate = (
            weights,
            shares,
            float(np.max(np.abs(gap))),
            float(np.max(np.abs(normalized_gap))),
            secondary_score,
            int(getattr(result, "nit", 0)),
            str(result.message),
        )
        if best_solution is None or candidate[3] < best_solution[3] - 1e-9:
            best_solution = candidate
        elif best_solution is not None and abs(candidate[3] - best_solution[3]) <= 1e-9:
            if candidate[4] < best_solution[4] - 1e-12:
                best_solution = candidate

    if best_solution is None:
        return None

    weights, shares, max_abs_share_gap, max_normalized_share_gap, objective_value, iterations, message = best_solution
    solution = RiskBudgetSolution(
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
    refined = _refine_balanced_risk_budget_solution(
        problem,
        reference_weights=reference_weights,
        # The primary minimax solution is already feasible at the optimal
        # maximum-gap ceiling.  Re-running the secondary tie-break from every
        # exploratory seed adds substantial pathological SLSQP work without
        # improving the primary objective.
        initial_guesses=[solution.weights],
        max_normalized_gap_ceiling=max_normalized_share_gap,
        max_iterations=min(max_iterations, 500),
    )
    if refined is not None and _is_better_risk_budget_solution(problem, refined, solution):
        return refined
    return solution


def _refine_balanced_risk_budget_solution(
    problem: RiskBudgetProblem,
    *,
    reference_weights: np.ndarray,
    initial_guesses: list[np.ndarray],
    max_normalized_gap_ceiling: float,
    max_iterations: int,
) -> RiskBudgetSolution | None:
    tolerance = max(1e-6, max_normalized_gap_ceiling * 1e-6)

    def achieved_shares(weights: np.ndarray) -> np.ndarray:
        return _risk_contribution_shares(
            problem.covariance,
            weights,
            contribution_mode=problem.contribution_mode,
        )

    def normalized_share_gap(weights: np.ndarray) -> np.ndarray:
        return _normalized_risk_share_gap(problem, achieved_shares(weights))

    def objective(weights: np.ndarray) -> float:
        normalized_gap = normalized_share_gap(weights)
        reference_gap = weights - reference_weights
        return float(normalized_gap @ normalized_gap) + 1e-4 * float(reference_gap @ reference_gap)

    constraints = [{"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)}]
    for index in range(len(problem.bucket_ids)):
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda weights, index=index: float(
                    (max_normalized_gap_ceiling + tolerance) - normalized_share_gap(weights)[index]
                ),
            }
        )
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda weights, index=index: float(
                    (max_normalized_gap_ceiling + tolerance) + normalized_share_gap(weights)[index]
                ),
            }
        )
    bounds = list(
        zip(
            problem.lower_bounds.tolist(),
            problem.upper_bounds.tolist(),
            strict=True,
        )
    )
    best_solution: tuple[np.ndarray, np.ndarray, float, float, float, int, str] | None = None
    for guess in initial_guesses:
        result = minimize(
            objective,
            x0=guess,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-15, "maxiter": max_iterations, "disp": False},
        )
        if not result.success:
            continue
        weights = np.asarray(result.x, dtype="float64")
        shares = achieved_shares(weights)
        gap = shares - problem.target_risk_shares
        normalized_gap = _normalized_risk_share_gap(problem, shares)
        candidate = (
            weights,
            shares,
            float(np.max(np.abs(gap))),
            float(np.max(np.abs(normalized_gap))),
            float(normalized_gap @ normalized_gap),
            int(getattr(result, "nit", 0)),
            str(result.message),
        )
        if best_solution is None or candidate[3] < best_solution[3] - 1e-9:
            best_solution = candidate
        elif best_solution is not None and abs(candidate[3] - best_solution[3]) <= 1e-9:
            if candidate[4] < best_solution[4] - 1e-12:
                best_solution = candidate
    if best_solution is None:
        return None
    weights, shares, max_abs_share_gap, _max_normalized_share_gap, l2_gap, iterations, message = best_solution
    return RiskBudgetSolution(
        bucket_ids=problem.bucket_ids,
        weights=weights,
        achieved_risk_shares=shares,
        objective_value=l2_gap,
        max_abs_share_gap=max_abs_share_gap,
        iterations=iterations,
        message=f"{message} (balanced minimax refinement)",
        solver_kind="slsqp_minimax_balanced",
        contribution_mode=problem.contribution_mode,
    )


def _solve_risk_budget_problem(
    problem: RiskBudgetProblem,
    *,
    max_iterations: int = 500,
    enforce_tolerance: bool = True,
) -> RiskBudgetSolution:
    _validate_risk_budget_problem(problem)
    reference_weights = _project_to_bounded_simplex(
        problem.reference_weights,
        problem.lower_bounds,
        problem.upper_bounds,
    )
    initial_guesses = _build_risk_budget_initial_guesses(problem, reference_weights)
    has_binding_bounds = _risk_budget_problem_has_binding_bounds(problem)
    all_weights_fixed = bool(
        has_binding_bounds
        and np.all(np.abs(problem.upper_bounds - problem.lower_bounds) <= 1e-12)
    )
    convex_seed_problem = (
        replace(
            problem,
            lower_bounds=np.zeros(len(problem.bucket_ids), dtype="float64"),
            upper_bounds=np.ones(len(problem.bucket_ids), dtype="float64"),
        )
        if has_binding_bounds
        else problem
    )
    convex_solution = _solve_convex_risk_budget(
        convex_seed_problem,
        max_iterations=max_iterations,
    )
    if convex_solution is not None:
        convex_seed = _project_to_bounded_simplex(
            convex_solution.weights,
            problem.lower_bounds,
            problem.upper_bounds,
        )
        initial_guesses = [convex_seed, *initial_guesses]
    else:
        convex_seed = None
    if all_weights_fixed:
        fixed_weights = _project_to_bounded_simplex(
            problem.lower_bounds,
            problem.lower_bounds,
            problem.upper_bounds,
        )
        fixed_shares = _risk_contribution_shares(
            problem.covariance,
            fixed_weights,
            contribution_mode=problem.contribution_mode,
        )
        fixed_gap = fixed_shares - problem.target_risk_shares
        best = RiskBudgetSolution(
            bucket_ids=problem.bucket_ids,
            weights=fixed_weights,
            achieved_risk_shares=fixed_shares,
            objective_value=_normalized_share_gap_l2(problem, fixed_shares),
            max_abs_share_gap=float(np.max(np.abs(fixed_gap))),
            iterations=0,
            message="All risk-sleeve weights are fixed; evaluated the unique feasible allocation.",
            solver_kind="fixed_bounds_evaluation",
            contribution_mode=problem.contribution_mode,
        )
    elif has_binding_bounds and convex_seed is not None:
        convex_seed_shares = _risk_contribution_shares(
            problem.covariance,
            convex_seed,
            contribution_mode=problem.contribution_mode,
        )
        convex_seed_gap = convex_seed_shares - problem.target_risk_shares
        if (
            float(np.max(np.abs(convex_seed_gap))) <= RESEARCH_MAX_RISK_BUDGET_SHARE_GAP
            and (
                problem.contribution_mode != "signed"
                or float(convex_seed_shares.min()) >= -1e-12
            )
        ):
            best = RiskBudgetSolution(
                bucket_ids=problem.bucket_ids,
                weights=convex_seed,
                achieved_risk_shares=convex_seed_shares,
                objective_value=_normalized_share_gap_l2(problem, convex_seed_shares),
                max_abs_share_gap=float(np.max(np.abs(convex_seed_gap))),
                iterations=convex_solution.iterations,
                message=(
                    "The canonical convex risk-budget solution satisfies the configured bounds."
                ),
                solver_kind="convex_log_barrier_bounded_feasible",
                contribution_mode=problem.contribution_mode,
            )
        else:
            best = None
    else:
        best = None

    if has_binding_bounds and best is not None:
        pass
    elif has_binding_bounds and not all_weights_fixed:
        minimax = _solve_minimax_risk_budget_slsqp(
            problem,
            reference_weights=reference_weights,
            initial_guesses=initial_guesses,
            max_iterations=max(max_iterations * 4, 1500),
        )
        if minimax is None:
            try:
                minimax = _solve_regularized_risk_budget_slsqp(
                    problem,
                    reference_weights=reference_weights,
                    initial_guesses=initial_guesses,
                    max_iterations=max_iterations,
                )
                minimax = replace(
                    minimax,
                    message=(
                        f"{minimax.message}; bounded minimax did not converge, so this is the "
                        "best regularized feasible candidate and remains subject to the target-gap check."
                    ),
                    solver_kind="slsqp_bounded_feasible_fallback",
                )
            except ValueError:
                feasible_candidates: list[RiskBudgetSolution] = []
                for candidate_weights in initial_guesses:
                    try:
                        candidate_shares = _risk_contribution_shares(
                            problem.covariance,
                            candidate_weights,
                            contribution_mode=problem.contribution_mode,
                        )
                    except ValueError:
                        continue
                    candidate_gap = candidate_shares - problem.target_risk_shares
                    feasible_candidates.append(
                        RiskBudgetSolution(
                            bucket_ids=problem.bucket_ids,
                            weights=candidate_weights,
                            achieved_risk_shares=candidate_shares,
                            objective_value=_normalized_share_gap_l2(problem, candidate_shares),
                            max_abs_share_gap=float(np.max(np.abs(candidate_gap))),
                            iterations=0,
                            message=(
                                "Bounded numerical optimizers did not converge; returned the best "
                                "deterministic feasible seed, subject to the target-gap check."
                            ),
                            solver_kind="deterministic_bounded_feasible_fallback",
                            contribution_mode=problem.contribution_mode,
                        )
                    )
                if not feasible_candidates:
                    raise ValueError(
                        "Risk budget constraints are feasible, but no positive-variance feasible candidate could be evaluated."
                    )
                minimax = min(
                    feasible_candidates,
                    key=lambda item: (
                        _solution_max_normalized_share_gap(problem, item),
                        _normalized_share_gap_l2(problem, item.achieved_risk_shares),
                    ),
                )
        best = minimax
    elif not has_binding_bounds and (
        convex_solution is not None
        and convex_solution.max_abs_share_gap <= RESEARCH_MAX_RISK_BUDGET_SHARE_GAP
        and float(convex_solution.achieved_risk_shares.min()) >= -1e-12
    ):
        best = convex_solution
    else:
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
                if minimax is not None and _is_better_risk_budget_solution(problem, minimax, primary)
                else primary
            )
    target_gap_missed = best.max_abs_share_gap > RESEARCH_MAX_RISK_BUDGET_SHARE_GAP + 1e-12
    if enforce_tolerance and target_gap_missed:
        raise ValueError(
            "Risk budget solver could not satisfy target risk shares within "
            f"{RESEARCH_MAX_RISK_BUDGET_SHARE_GAP:.2%}; achieved max gap {best.max_abs_share_gap:.2%}."
        )
    achieved = np.asarray(best.achieved_risk_shares, dtype="float64")
    negative_signed_share = best.contribution_mode == "signed" and float(achieved.min()) < -1e-12
    if enforce_tolerance and negative_signed_share:
        raise ValueError("Risk budget solver produced a negative signed risk share.")
    if target_gap_missed or negative_signed_share:
        reasons: list[str] = []
        if target_gap_missed:
            reasons.append(
                f"maximum target-share gap is {best.max_abs_share_gap:.2%} "
                f"(tolerance {RESEARCH_MAX_RISK_BUDGET_SHARE_GAP:.2%})"
            )
        if negative_signed_share:
            reasons.append(f"minimum signed risk share is {float(achieved.min()):.2%}")
        constrained = _risk_budget_problem_has_binding_bounds(problem)
        verified_constrained_optimum = bool(
            constrained
            and target_gap_missed
            and not negative_signed_share
            and "fallback" not in best.solver_kind
        )
        if verified_constrained_optimum:
            return replace(
                best,
                target_status="constrained_optimum",
                execution_ready=True,
                message=(
                    f"{best.message}; verified hard-constraint optimum is execution-ready, "
                    "but it cannot exactly match the requested risk shares: "
                    + "; ".join(reasons)
                    + "."
                ),
            )
        return replace(
            best,
            target_status="constrained_target_miss" if constrained else "target_miss",
            execution_ready=False,
            message=(
                f"{best.message}; best feasible result is not execution-ready: "
                + "; ".join(reasons)
                + "."
            ),
        )
    return best


def _solve_risk_budget_weights(
    *,
    target_shares: np.ndarray,
    return_window: pd.DataFrame,
    reference_weights: np.ndarray | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
    missing_return_policy: str,
    risk_model_config: dict[str, object] | None = None,
    lower_bounds: np.ndarray | None = None,
    upper_bounds: np.ndarray | None = None,
) -> LocalRiskBudgetSolve:
    raw_target = np.asarray(target_shares, dtype="float64")
    if raw_target.ndim != 1:
        raise ValueError("Risk budget target shares must be a one-dimensional vector.")
    count = len(raw_target)
    if count == 0:
        raise ValueError("Risk budget target shares cannot be empty.")
    target = _normalize_positive_vector(raw_target)
    normalized_missing_return_policy = _normalize_missing_return_policy(missing_return_policy)
    if lower_bounds is not None and len(lower_bounds) != count:
        raise ValueError("Risk budget lower-bound dimension does not match target shares.")
    if upper_bounds is not None and len(upper_bounds) != count:
        raise ValueError("Risk budget upper-bound dimension does not match target shares.")
    resolved_lower_bounds = (
        np.asarray(lower_bounds, dtype="float64")
        if lower_bounds is not None
        else np.zeros(count, dtype="float64")
    )
    resolved_upper_bounds = (
        np.asarray(upper_bounds, dtype="float64")
        if upper_bounds is not None
        else np.ones(count, dtype="float64")
    )
    if not np.isfinite(resolved_lower_bounds).all() or not np.isfinite(resolved_upper_bounds).all():
        raise ValueError("Risk budget bounds must contain only finite values.")
    if np.any(resolved_lower_bounds < -1e-12) or np.any(resolved_upper_bounds > 1.0 + 1e-12):
        raise ValueError("Risk budget bounds must be between 0 and 1.")
    if np.any(resolved_upper_bounds < resolved_lower_bounds - 1e-12):
        raise ValueError("Risk budget upper bounds cannot be smaller than lower bounds.")
    if float(resolved_lower_bounds.sum()) > 1.0 + 1e-10 or float(resolved_upper_bounds.sum()) < 1.0 - 1e-10:
        raise ValueError("Risk budget bounds do not contain a fully invested solution.")
    has_binding_bounds = bool(
        np.any(resolved_lower_bounds > 1e-12) or np.any(resolved_upper_bounds < 1.0 - 1e-12)
    )
    if count == 1:
        if resolved_lower_bounds[0] > 1.0 + 1e-12 or resolved_upper_bounds[0] < 1.0 - 1e-12:
            raise ValueError("Single-member risk budget bounds are infeasible.")
        return LocalRiskBudgetSolve(
            weights=np.asarray([1.0], dtype="float64"),
            max_abs_share_gap=0.0,
            solver_kind="bounded-single-member" if has_binding_bounds else "single-member",
            solver_detail=None,
            covariance_model=None,
            covariance_observations=0,
            risk_contribution_mode=None,
            missing_return_policy=normalized_missing_return_policy,
        )

    cleaned_returns = _clean_return_frame(return_window)
    complete_observation_count = int(len(cleaned_returns.dropna(how="any")))
    if complete_observation_count < 2:
        raise ValueError(
            "Risk budget solve requires at least two aligned return observations; "
            f"got {complete_observation_count}."
        )
    covariance_parameters = _risk_model_covariance_parameters(risk_model_config, calculation_frequency, lookback_days)
    covariance_model_id = _risk_model_covariance_model_id(risk_model_config)
    contribution_mode = _risk_model_contribution_mode(risk_model_config)
    coverage = _prepare_return_window_for_covariance(
        return_window,
        lookback_days=lookback_days,
        min_observations=int(covariance_parameters.get("min_observations", 2)),
        label="Risk budget solve",
        missing_return_policy=normalized_missing_return_policy,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
        max_trailing_staleness_days=int(
            covariance_parameters.get(
                "max_period_staleness_days",
                _max_complete_case_drop_staleness_days(calculation_frequency),
            )
        ),
    )
    covariance_window = coverage.returns

    reference = (
        np.asarray(reference_weights, dtype="float64")
        if reference_weights is not None and len(reference_weights) == count
        else target
    )
    if not np.isfinite(reference).all() or float(np.clip(reference, 0.0, None).sum()) <= 1e-12:
        reference = target
    reference = _normalize_positive_vector(reference)
    covariance = _estimate_covariance(
        covariance_window,
        model_id=covariance_model_id,
        lookback_days=lookback_days,
        parameters=covariance_parameters,
        missing_return_policy=MISSING_RETURN_POLICY_STRICT,
        calculation_frequency=calculation_frequency,
        as_of_date=as_of_date,
    )
    if covariance.shape != (count, count):
        raise ValueError("Risk covariance dimension does not match selected scope members.")
    primary_problem = RiskBudgetProblem(
        bucket_ids=list(return_window.columns),
        covariance=covariance.to_numpy(dtype="float64"),
        target_risk_shares=target,
        lower_bounds=resolved_lower_bounds,
        upper_bounds=resolved_upper_bounds,
        reference_weights=reference,
        contribution_mode=contribution_mode,
    )
    solution = _solve_risk_budget_problem(primary_problem, enforce_tolerance=not has_binding_bounds)

    return LocalRiskBudgetSolve(
        weights=_normalize_positive_vector(solution.weights),
        max_abs_share_gap=float(solution.max_abs_share_gap),
        solver_kind="risk-budget",
        solver_detail=solution.solver_kind,
        covariance_model=covariance_model_id,
        covariance_observations=int(len(covariance_window)),
        risk_contribution_mode=solution.contribution_mode,
        message=solution.message,
        missing_return_policy=coverage.policy,
        return_rows_before_policy=coverage.rows_before,
        return_rows_after_policy=coverage.rows_after,
        missing_return_row_count=coverage.missing_row_count,
        missing_return_row_fraction=coverage.missing_row_fraction,
        leading_incomplete_return_row_count=coverage.leading_incomplete_row_count,
        post_warmup_missing_return_row_count=coverage.post_warmup_missing_row_count,
        post_warmup_missing_return_row_fraction=coverage.post_warmup_missing_row_fraction,
        dropped_return_rows=coverage.dropped_rows,
        latest_complete_return_date=coverage.latest_complete_date.isoformat() if coverage.latest_complete_date else None,
        trailing_complete_return_staleness_days=coverage.trailing_staleness_days,
        target_status=solution.target_status,
        execution_ready=solution.execution_ready,
    )


def _resolve_volatility_overlay_gross_exposure(
    *,
    capital_mode: str,
    estimated_volatility: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
) -> float:
    if target_volatility is None or target_volatility <= 0:
        raise ValueError("Volatility overlay requires positive target volatility.")
    if estimated_volatility is None or estimated_volatility <= 0:
        raise ValueError("Volatility overlay requires positive estimated risky-sleeve volatility.")
    target_gross = float(target_volatility) / float(estimated_volatility)
    if capital_mode == CAPITAL_MODE_VOLATILITY_CAP:
        return min(target_gross, 1.0)
    max_gross = 1.0 if max_gross_exposure is None else float(max_gross_exposure)
    if target_gross > max_gross + 1e-12:
        raise ValueError(
            f"Target volatility requires {target_gross:.6f} gross exposure, "
            f"above the configured maximum {max_gross:.6f}."
        )
    return target_gross


def _validate_research_solve_configuration(
    *,
    calculation_frequency: str,
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
) -> None:
    if str(calculation_frequency).strip().lower() != "daily":
        raise ValueError(f"Unsupported calculation frequency: {calculation_frequency}.")
    if target_dimension not in {
        TARGET_DIMENSION_SCOPE_DEFAULT,
        TARGET_DIMENSION_WEIGHT,
        TARGET_DIMENSION_RISK_BUDGET,
    }:
        raise ValueError(f"Unsupported Research target dimension: {target_dimension}.")
    if capital_mode not in {
        CAPITAL_MODE_UNIT_NOTIONAL,
        CAPITAL_MODE_FIXED_GROSS,
        CAPITAL_MODE_TARGET_VOLATILITY,
        CAPITAL_MODE_VOLATILITY_CAP,
    }:
        raise ValueError(f"Unsupported Research capital mode: {capital_mode}.")

    values = {
        "gross_exposure": gross_exposure,
        "target_volatility": target_volatility,
        "max_gross_exposure": max_gross_exposure,
    }
    for label, value in values.items():
        if value is not None and (not np.isfinite(float(value)) or float(value) <= 0.0):
            raise ValueError(f"Research {label} must be finite and positive when provided.")

    if capital_mode == CAPITAL_MODE_UNIT_NOTIONAL:
        if any(value is not None for value in values.values()):
            raise ValueError(
                "unit_notional capital mode must not set gross_exposure, target_volatility, or max_gross_exposure."
            )
    elif capital_mode == CAPITAL_MODE_FIXED_GROSS:
        if gross_exposure is None:
            raise ValueError("fixed_gross capital mode requires gross_exposure.")
        if target_volatility is not None or max_gross_exposure is not None:
            raise ValueError("fixed_gross capital mode must not set target_volatility or max_gross_exposure.")
    elif capital_mode == CAPITAL_MODE_TARGET_VOLATILITY:
        if target_volatility is None:
            raise ValueError("target_volatility capital mode requires target_volatility.")
        if gross_exposure is not None:
            raise ValueError("target_volatility capital mode must not set gross_exposure.")
    elif capital_mode == CAPITAL_MODE_VOLATILITY_CAP:
        if target_volatility is None:
            raise ValueError("volatility_cap capital mode requires target_volatility.")
        if gross_exposure is not None or max_gross_exposure is not None:
            raise ValueError("volatility_cap capital mode must not set gross_exposure or max_gross_exposure.")


def _periodic_nav_series(
    series: pd.Series,
    *,
    calculation_frequency: CalculationFrequency,
    start_date: date,
    end_date: date,
) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="float64")
    anchor = series.loc[series.index <= start_date].tail(1)
    visible = pd.concat([anchor, series.loc[(series.index > start_date) & (series.index <= end_date)]]).sort_index()
    if visible.empty:
        return pd.Series(dtype="float64")
    del calculation_frequency
    return visible.map(_safe_float).astype("float64")


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
        visible = series.loc[series.index <= end_date]
        if visible.empty:
            raise ValueError("Selected scope contains a member without usable history in the requested window.")
        first_dates.append(visible.index[0])
        calendar_points.update(visible.index.tolist())
    effective_start = max([start_date, *first_dates])
    calendar = sorted({effective_start, *(item for item in calendar_points if effective_start < item <= end_date)})
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
    selected_nav_series_by_member = {
        (member.member_type, member.member_id): nav_series_by_member[(member.member_type, member.member_id)]
        for member in members
    }
    periodic_nav_series_by_member = _periodic_series_by_member(
        selected_nav_series_by_member,
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
        if member.member_type == TARGET_MEMBER_INSTRUMENT:
            aligned = (
                periodic_series.reindex(periodic_series.index.union(calendar))
                .sort_index()
                .ffill()
                .reindex(calendar)
            )
            # Mark-to-last bridges publication calendars, not an indefinitely
            # stale trailing series. Keep missing tails visible to the policy.
            aligned.loc[aligned.index > periodic_series.index[-1]] = np.nan
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
    configured = [item for item in candidates if str(item.get("status") or "active") == "active"]
    if not configured:
        return None
    configured.sort(key=lambda item: str(item.get("target_set_id") or ""), reverse=True)
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
    fixed_capital_members = [
        member for member in scope_members if _member_is_fixed_capital(member)
    ]
    dimension_members = (
        [member for member in scope_members if not _member_is_fixed_capital(member)]
        if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET
        else scope_members
    )
    scope_label = str(state.node_by_id.get(scope_node_id, {}).get("node_name") or ROOT_SCOPE_LABEL)
    if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET and not dimension_members:
        raise ValueError(f"{scope_label} risk budget is unavailable: no risky members.")
    line_keys = [(member.member_type, member.member_id) for member in dimension_members]

    enabled_field = "weight_enabled" if resolved_dimension == TARGET_DIMENSION_WEIGHT else "risk_budget_enabled"
    value_field = "target_weight" if resolved_dimension == TARGET_DIMENSION_WEIGHT else "target_risk_share"
    saw_enabled_target_set = False
    incomplete_target_sets: list[tuple[dict[str, object], list[str]]] = []
    for target_set_type in candidate_types:
        target_set = _scope_target_sets(
            state,
            scope_node_id=scope_node_id,
            target_set_type=target_set_type,
            as_of_date=as_of_date,
        )
        if target_set is None or not bool(target_set.get(enabled_field)):
            continue
        saw_enabled_target_set = True
        line_map = state.target_lines_by_set_id.get(str(target_set.get("target_set_id") or ""), {})
        rendered_rows: list[dict[str, object]] = []
        complete = True
        missing_member_labels: list[str] = []
        for member in dimension_members:
            line = line_map.get((member.member_type, member.member_id))
            if line is None or line.get(value_field) is None:
                if resolved_dimension == TARGET_DIMENSION_WEIGHT and _member_is_fixed_capital(member):
                    selected_value = 0.0
                    target_weight = 0.0
                    target_risk_share = None
                else:
                    missing_member_labels.append(member.label)
                    complete = False
                    break
            else:
                selected_value = float(line.get(value_field))
                target_weight = _safe_float(line.get("target_weight"))
                target_risk_share = (
                    None
                    if _member_is_fixed_capital(member)
                    else _safe_float(line.get("target_risk_share"))
                )
            rendered_rows.append(
                {
                    "member_type": member.member_type,
                    "member_id": member.member_id,
                    "label": member.label,
                    "taxonomy_node_id": member.taxonomy_node_id,
                    "default_target_dimension": member.default_target_dimension,
                    "selected_dimension": resolved_dimension,
                    "selected_value": selected_value,
                    "target_weight": target_weight,
                    "target_risk_share": target_risk_share,
                    "source_target_set_id": target_set.get("target_set_id"),
                    "source_target_set_type": target_set_type,
                }
            )
        if complete and len(rendered_rows) == len(line_keys):
            selected_total = sum(float(row["selected_value"]) for row in rendered_rows)
            expected_total = 1.0
            if any(float(row["selected_value"]) < -1e-12 for row in rendered_rows):
                raise ValueError(f"{target_set.get('name') or target_set_type} has negative {resolved_dimension} targets.")
            if abs(selected_total - expected_total) > 1e-6:
                raise ValueError(
                    f"{target_set.get('name') or target_set_type} {resolved_dimension} targets must sum to "
                    f"{expected_total:.6f}; got {selected_total:.6f}."
                )
            if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET:
                for member in fixed_capital_members:
                    line = line_map.get((member.member_type, member.member_id)) or {}
                    rendered_rows.append(
                        {
                            "member_type": member.member_type,
                            "member_id": member.member_id,
                            "label": member.label,
                            "taxonomy_node_id": member.taxonomy_node_id,
                            "default_target_dimension": member.default_target_dimension,
                            "selected_dimension": resolved_dimension,
                            "selected_value": None,
                            "target_weight": _safe_float(line.get("target_weight")),
                            "target_risk_share": None,
                            "source_target_set_id": target_set.get("target_set_id"),
                            "source_target_set_type": target_set_type,
                        }
                    )
            return rendered_rows, warnings
        if not complete:
            incomplete_target_sets.append((target_set, missing_member_labels))

    if saw_enabled_target_set and incomplete_target_sets:
        target_set, missing_member_labels = incomplete_target_sets[0]
        missing_label = ", ".join(missing_member_labels[:8]) or "one or more active taxonomy members"
        if len(missing_member_labels) > 8:
            missing_label += f", +{len(missing_member_labels) - 8} more"
        raise ValueError(
            f"{scope_label} {resolved_dimension} target set is incomplete; "
            f"missing target lines for active members: {missing_label}."
        )

    if len(dimension_members) == 1:
        member = dimension_members[0]
        selected_value = 1.0
        rendered_rows = [
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
        ]
        if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET:
            rendered_rows.extend(
                {
                    "member_type": fixed_capital_member.member_type,
                    "member_id": fixed_capital_member.member_id,
                    "label": fixed_capital_member.label,
                    "taxonomy_node_id": fixed_capital_member.taxonomy_node_id,
                    "default_target_dimension": fixed_capital_member.default_target_dimension,
                    "selected_dimension": resolved_dimension,
                    "selected_value": None,
                    "target_weight": None,
                    "target_risk_share": None,
                    "source_target_set_id": None,
                    "source_target_set_type": None,
                    "source_label_override": "Fixed Capital Context",
                }
                for fixed_capital_member in fixed_capital_members
            )
        return rendered_rows, warnings

    raise ValueError(
        f"{scope_label} has no active complete {resolved_dimension} target set for the requested scope members."
    )


def _scope_members(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
) -> tuple[list[ScopeMemberRecord], str]:
    child_node_ids = [
        node_id
        for node_id in state.children_by_parent.get(scope_node_id, [])
        if _node_has_research_members(state, node_id)
    ]
    if child_node_ids:
        members = [
            ScopeMemberRecord(
                member_type=TARGET_MEMBER_NODE,
                member_id=node_id,
                label=str(state.node_by_id[node_id]["node_name"]),
                taxonomy_node_id=node_id,
                default_target_dimension=str(state.node_by_id[node_id].get("default_target_dimension") or TARGET_DIMENSION_WEIGHT),
            )
            for node_id in child_node_ids
        ]
        if scope_node_id is None:
            members.append(
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_DERIVATIVE,
                    member_id=SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
                    label=SYSTEM_DERIVATIVE_TARGET_LABEL,
                    taxonomy_node_id=None,
                    default_target_dimension=TARGET_DIMENSION_WEIGHT,
                )
            )
            members.append(
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_CASH,
                    member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
                    label=SYSTEM_CASH_TARGET_LABEL,
                    taxonomy_node_id=None,
                    default_target_dimension=TARGET_DIMENSION_WEIGHT,
                )
            )
        return members, "child_sleeves"

    if scope_node_id is None:
        return (
            [
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_DERIVATIVE,
                    member_id=SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
                    label=SYSTEM_DERIVATIVE_TARGET_LABEL,
                    taxonomy_node_id=None,
                    default_target_dimension=TARGET_DIMENSION_WEIGHT,
                ),
                ScopeMemberRecord(
                    member_type=TARGET_MEMBER_CASH,
                    member_id=SYSTEM_CASH_TARGET_MEMBER_ID,
                    label=SYSTEM_CASH_TARGET_LABEL,
                    taxonomy_node_id=None,
                    default_target_dimension=TARGET_DIMENSION_WEIGHT,
                )
            ],
            "child_sleeves",
        )

    direct_assignments = state.direct_assignments_by_node.get(scope_node_id, [])
    if not direct_assignments:
        raise ValueError("Selected scope does not have child sleeves or direct assigned members.")

    members: list[ScopeMemberRecord] = []
    for assignment in direct_assignments:
        target_scope = str(assignment.get("target_scope") or "")
        target_entity_id = str(assignment.get("target_entity_id") or "")
        if target_scope != TARGET_MEMBER_INSTRUMENT:
            continue
        detail = _instrument_detail(state, target_entity_id)
        label = str((detail or {}).get("instrument_name") or target_entity_id)
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


def _scope_instrument_count(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    start_date: date,
    end_date: date,
) -> int:
    if scope_node_id is None:
        node_ids = set(state.node_by_id)
    else:
        node_ids = state.node_subtree_by_id.get(scope_node_id, {scope_node_id})
    seen_instrument_ids: set[str] = set()
    for node_id in node_ids:
        for assignment in state.direct_assignments_by_node.get(node_id, []):
            if str(assignment.get("target_scope") or "") != TARGET_MEMBER_INSTRUMENT:
                continue
            instrument_id = str(assignment.get("target_entity_id") or "").strip()
            if not instrument_id or instrument_id in seen_instrument_ids:
                continue
            detail = _instrument_detail(state, instrument_id)
            if not isinstance(detail, dict):
                continue
            dates = [
                point_date
                for point_date, _point_value, _point_currency in _selected_price_points(detail, end_date=end_date)
                if start_date <= point_date <= end_date
            ]
            if dates:
                seen_instrument_ids.add(instrument_id)
    return len(seen_instrument_ids)


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
    start_day = research_window_start_date(as_of_date, lookback_days)
    aligned_members, _calendar, _warnings = _align_member_series(
        members,
        nav_series_by_member,
        start_date=start_day,
        end_date=as_of_date,
        calculation_frequency=calculation_frequency,
    )
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
    cleaned = return_series.astype("float64").replace([np.inf, -np.inf], np.nan).sort_index()
    gross_returns = 1.0 + cleaned
    if len(gross_returns) and pd.isna(gross_returns.iloc[0]):
        gross_returns.iloc[0] = 1.0
    # Keep the missing NAV at the broken period, but resume the cumulative
    # level once a later return is available. The parent-level pct_change then
    # also leaves the first observation after the gap missing and resumes only
    # after two consecutive valid NAV levels. This preserves period identity
    # without allowing one finite child gap to poison every later parent period.
    return gross_returns.cumprod(skipna=True)


def _weighted_complete_return_series(return_window: pd.DataFrame, weights: pd.Series) -> pd.Series:
    if return_window.empty or weights.empty:
        return pd.Series(dtype="float64")
    missing_active_columns = [
        str(column)
        for column, weight in weights.items()
        if abs(float(weight or 0.0)) > 1e-12 and column not in return_window.columns
    ]
    if missing_active_columns:
        raise ValueError(f"Return window is missing active weighted columns: {', '.join(missing_active_columns[:5])}.")
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
    missing_return_policy: str,
    contribution_mode: str,
    risk_model_config: dict[str, object] | None = None,
) -> tuple[dict[str, float], list[str]]:
    if not risk_keys:
        return {}, []
    risky_weights = weights_by_key.reindex(risk_keys, fill_value=0.0).astype("float64")
    active_risk_keys = [key for key in risk_keys if abs(float(risky_weights.get(key, 0.0))) > 1e-12]
    if not active_risk_keys:
        return {key: 0.0 for key in risk_keys}, []
    if len(active_risk_keys) == 1:
        return {
            key: 1.0 if key == active_risk_keys[0] else 0.0
            for key in risk_keys
        }, []
    member_by_key = {f"{member.member_type}::{member.member_id}": member for member in members}
    solver_members = [member_by_key[key] for key in active_risk_keys if key in member_by_key]
    if len(solver_members) != len(active_risk_keys):
        return {}, ["Current risk-share estimate skipped because scope member keys could not be resolved."]
    try:
        return_window = _solver_return_window(
            members=solver_members,
            nav_series_by_member=nav_series_by_member,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=calculation_frequency,
        )
        if return_window.empty:
            return {}, ["Current risk-share estimate skipped because aligned return history is empty."]
        covariance = _estimate_covariance(
            return_window.reindex(columns=active_risk_keys),
            model_id=_risk_model_covariance_model_id(risk_model_config),
            lookback_days=lookback_days,
            parameters=_risk_model_covariance_parameters(risk_model_config, calculation_frequency, lookback_days),
            missing_return_policy=missing_return_policy,
            calculation_frequency=calculation_frequency,
            as_of_date=as_of_date,
        )
        shares = _risk_contribution_shares(
            covariance.reindex(index=active_risk_keys, columns=active_risk_keys).to_numpy(dtype="float64"),
            risky_weights.reindex(active_risk_keys).to_numpy(dtype="float64"),
            contribution_mode=contribution_mode,
        )
    except ValueError as error:
        return {}, [f"Current risk-share estimate skipped: {error}"]
    rendered = {key: 0.0 for key in risk_keys}
    rendered.update({key: float(shares[index]) for index, key in enumerate(active_risk_keys)})
    return rendered, []


def _root_top_sleeve_bounds_by_key(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    member_by_key: dict[str, ScopeMemberRecord],
    member_keys: list[str],
) -> dict[str, dict[str, float | None]]:
    if scope_node_id is not None or not state.top_sleeve_weight_bounds:
        return {}
    bounds_by_key: dict[str, dict[str, float | None]] = {}
    for key in member_keys:
        member = member_by_key.get(key)
        if member is None or member.member_type != TARGET_MEMBER_NODE:
            continue
        bounds = state.top_sleeve_weight_bounds.get(member.member_id)
        if bounds:
            bounds_by_key[key] = bounds
    return bounds_by_key


def _validate_fixed_top_sleeve_bounds(
    *,
    scope_label: str,
    fixed_weight_targets: pd.Series,
    bounds_by_key: dict[str, dict[str, float | None]],
    member_by_key: dict[str, ScopeMemberRecord],
) -> None:
    for key, bounds in bounds_by_key.items():
        if key not in fixed_weight_targets.index:
            continue
        weight = float(fixed_weight_targets.get(key, 0.0))
        member_label = member_by_key[key].label
        min_weight = _safe_float(bounds.get("min_weight"))
        max_weight = _safe_float(bounds.get("max_weight"))
        if min_weight is not None and weight < min_weight - 1e-8:
            raise ValueError(
                f"{scope_label} frozen sleeve {member_label} weight {weight:.2%} is below its minimum {min_weight:.2%}."
            )
        if max_weight is not None and weight > max_weight + 1e-8:
            raise ValueError(
                f"{scope_label} frozen sleeve {member_label} weight {weight:.2%} exceeds its maximum {max_weight:.2%}."
            )


def _validate_final_top_sleeve_bounds(
    *,
    scope_label: str,
    implementation_weights: pd.Series,
    bounds_by_key: dict[str, dict[str, float | None]],
    member_by_key: dict[str, ScopeMemberRecord],
) -> None:
    for key, bounds in bounds_by_key.items():
        weight = float(implementation_weights.get(key, 0.0))
        member_label = member_by_key[key].label
        min_weight = _safe_float(bounds.get("min_weight"))
        max_weight = _safe_float(bounds.get("max_weight"))
        if min_weight is not None and weight < min_weight - 1e-8:
            raise ValueError(
                f"{scope_label} top sleeve {member_label} final weight {weight:.2%} is below its minimum {min_weight:.2%}."
            )
        if max_weight is not None and weight > max_weight + 1e-8:
            raise ValueError(
                f"{scope_label} top sleeve {member_label} final weight {weight:.2%} exceeds its maximum {max_weight:.2%}."
            )


def _resolve_active_top_sleeve_bound_vectors(
    *,
    scope_label: str,
    active_keys: list[str],
    active_budget: float,
    bounds_by_key: dict[str, dict[str, float | None]],
    member_by_key: dict[str, ScopeMemberRecord],
    allow_upper_shortfall: bool = True,
) -> tuple[float, np.ndarray | None, np.ndarray | None]:
    if not active_keys or not bounds_by_key:
        return active_budget, None, None
    lower_final = []
    upper_final = []
    for key in active_keys:
        bounds = bounds_by_key.get(key) or {}
        min_weight = _safe_float(bounds.get("min_weight")) or 0.0
        max_weight = _safe_float(bounds.get("max_weight"))
        lower_final.append(float(min_weight))
        upper_final.append(float(active_budget if max_weight is None else max_weight))
    lower = np.asarray(lower_final, dtype="float64")
    upper = np.asarray(upper_final, dtype="float64")
    if np.any(upper < lower - 1e-12):
        offenders = [
            member_by_key[key].label
            for index, key in enumerate(active_keys)
            if upper[index] < lower[index] - 1e-12
        ]
        raise ValueError(f"{scope_label} top sleeve bounds are infeasible for {', '.join(offenders)}.")
    lower_total = float(lower.sum())
    upper_total = float(upper.sum())
    if active_budget < lower_total - 1e-12:
        raise ValueError(
            f"{scope_label} top sleeve minimum weights require {lower_total:.2%}, "
            f"but only {active_budget:.2%} active risky budget is available."
        )
    if active_budget > upper_total + 1e-12 and not allow_upper_shortfall:
        raise ValueError(
            f"{scope_label} top sleeve maximum weights allow only {upper_total:.2%}, "
            f"below the requested {active_budget:.2%} active risky budget."
        )
    constrained_active_budget = min(float(active_budget), upper_total)
    if constrained_active_budget < lower_total - 1e-12:
        raise ValueError(
            f"{scope_label} top sleeve bounds leave only {constrained_active_budget:.2%} active risky budget, "
            f"below the required minimum {lower_total:.2%}."
        )
    if constrained_active_budget <= 1e-12:
        raise ValueError(f"{scope_label} top sleeve bounds leave no active risky allocation.")
    lower_local = np.clip(lower / constrained_active_budget, 0.0, 1.0)
    upper_local = np.clip(upper / constrained_active_budget, 0.0, 1.0)
    if float(lower_local.sum()) > 1.0 + 1e-10 or float(upper_local.sum()) < 1.0 - 1e-10:
        raise ValueError(f"{scope_label} top sleeve bounds are infeasible for the active risky allocation.")
    return constrained_active_budget, lower_local, upper_local


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
    missing_return_policy: str,
    apply_capital_overlay: bool,
    risk_model_config: dict[str, object] | None = None,
    include_actuals: bool = True,
    resolve_frozen_actuals: bool = False,
    fixed_derivative_weight_override: float | None = None,
    inherited_no_trade: bool = False,
) -> ScopeTargetSolveResult:
    scope_label = str(state.node_by_id.get(scope_node_id, {}).get("node_name") or ROOT_SCOPE_LABEL)
    scope_path = state.node_path_by_id.get(scope_node_id or ROOT_SCOPE_MEMBER_ID, ROOT_SCOPE_LABEL)
    scope_depth = int(state.node_depth_by_id.get(scope_node_id, 0))
    default_target_dimension = _scope_default_target_dimension(state, scope_node_id)
    start_day = research_window_start_date(as_of_date, lookback_days)

    members, member_source = _scope_members(state, scope_node_id=scope_node_id)
    warnings: list[str] = []
    resolved_rows, resolution_warnings = _resolve_dimension_target_rows(
        state,
        scope_node_id=scope_node_id,
        scope_members=members,
        as_of_date=as_of_date,
        selected_dimension=target_dimension,
    )
    warnings.extend(resolution_warnings)

    nav_series_by_member: dict[tuple[str, str], pd.Series] = {}
    current_nav_series_by_member: dict[tuple[str, str], pd.Series] = {}
    member_by_key = {
        f"{member.member_type}::{member.member_id}": member
        for member in members
    }
    member_keys = list(member_by_key)
    frozen_keys = [
        key
        for key in member_keys
        if _member_is_frozen(
            state,
            scope_node_id=scope_node_id,
            member=member_by_key[key],
            inherited_no_trade=inherited_no_trade,
        )
    ]
    target_dimension_used = str(resolved_rows[0]["selected_dimension"]) if resolved_rows else TARGET_DIMENSION_WEIGHT
    top_sleeve_bounds_by_key = _root_top_sleeve_bounds_by_key(
        state,
        scope_node_id=scope_node_id,
        member_by_key=member_by_key,
        member_keys=member_keys,
    )
    child_results_by_key: dict[str, ScopeTargetSolveResult] = {}
    child_scope_solve_events: list[dict[str, object]] = []
    zero_target_keys = {
        f"{row['member_type']}::{row['member_id']}"
        for row in resolved_rows
        if str(row.get("selected_dimension") or "") in {TARGET_DIMENSION_WEIGHT, TARGET_DIMENSION_RISK_BUDGET}
        and abs(float(_safe_float(row.get("selected_value")) or 0.0)) <= 1e-12
        and not _member_is_fixed_capital(
            member_by_key[f"{row['member_type']}::{row['member_id']}"],
        )
        and not _member_is_frozen(
            state,
            scope_node_id=scope_node_id,
            member=member_by_key[f"{row['member_type']}::{row['member_id']}"],
            inherited_no_trade=inherited_no_trade,
        )
        and float(
            _safe_float(
                (top_sleeve_bounds_by_key.get(f"{row['member_type']}::{row['member_id']}") or {}).get(
                    "min_weight"
                )
            )
            or 0.0
        )
        <= 1e-12
    }
    if zero_target_keys:
        excluded_labels = ", ".join(member_by_key[key].label for key in sorted(zero_target_keys))
        warnings.append(
            f"{scope_label} excludes 0% {target_dimension_used.replace('_', ' ')} members from target history, covariance, and return coverage: {excluded_labels}."
        )

    for member in members:
        member_key = f"{member.member_type}::{member.member_id}"
        if member.member_type == TARGET_MEMBER_NODE:
            if member_key in zero_target_keys:
                zero_nav = _build_cash_nav_series(start_date=start_day, end_date=as_of_date)
                nav_series_by_member[(member.member_type, member.member_id)] = zero_nav
                current_nav_series_by_member[(member.member_type, member.member_id)] = zero_nav
                continue
            child_path = state.node_path_by_id.get(member.member_id, member.label)
            try:
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
                    missing_return_policy=missing_return_policy,
                    apply_capital_overlay=False,
                    risk_model_config=risk_model_config,
                    include_actuals=include_actuals,
                    resolve_frozen_actuals=resolve_frozen_actuals,
                    fixed_derivative_weight_override=fixed_derivative_weight_override,
                    inherited_no_trade=_member_has_no_trade_constraint(
                        state,
                        scope_node_id=scope_node_id,
                        member=member,
                        inherited_no_trade=inherited_no_trade,
                    ),
                )
            except ValueError as error:
                raise ValueError(f"{child_path} solve failed: {error}") from error
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
        elif member.member_type in {TARGET_MEMBER_CASH, TARGET_MEMBER_DERIVATIVE}:
            fixed_capital_nav = _build_cash_nav_series(
                start_date=start_day,
                end_date=as_of_date,
            )
            nav_series_by_member[(member.member_type, member.member_id)] = fixed_capital_nav
            current_nav_series_by_member[(member.member_type, member.member_id)] = fixed_capital_nav
        else:
            analytics_scope = state.instrument_analytics_scopes.get(member.member_id)
            if analytics_scope is not None and not (
                analytics_scope.get("risk_eligible")
                and analytics_scope.get("risk_budget_eligible")
            ) and member_key not in zero_target_keys:
                raise ValueError(
                    f"{member.label} is not eligible for Research risk allocation under its effective analytics policy: "
                    f"{analytics_scope.get('exclusion_reason') or 'risk/risk-budget eligibility is disabled'}."
                )
            try:
                instrument_nav, instrument_warnings = _build_instrument_nav_series(
                    state,
                    instrument_id=member.member_id,
                    start_date=start_day,
                    end_date=as_of_date,
                )
            except ValueError as error:
                if member_key not in zero_target_keys:
                    raise
                instrument_nav = _build_cash_nav_series(start_date=start_day, end_date=as_of_date)
                instrument_warnings = [
                    f"{member.label} has a 0% {target_dimension_used.replace('_', ' ')} target and was excluded from target history/covariance/return coverage: {error}"
                ]
            nav_series_by_member[(member.member_type, member.member_id)] = instrument_nav
            current_nav_series_by_member[(member.member_type, member.member_id)] = instrument_nav
            warnings.extend(instrument_warnings)

    if include_actuals or (resolve_frozen_actuals and frozen_keys):
        current_actual_rows, current_actual_warnings = _current_scope_actuals(
            state,
            scope_node_id=scope_node_id,
            as_of_date=as_of_date,
        )
        warnings.extend(current_actual_warnings)
        current_actual_weight_by_key = {
            f"{item['member_type']}::{item['member_id']}": float(_safe_float(item.get("current_weight")) or 0.0)
            for item in current_actual_rows
            if item.get("member_type") in {
                TARGET_MEMBER_NODE,
                TARGET_MEMBER_INSTRUMENT,
                TARGET_MEMBER_CASH,
                TARGET_MEMBER_DERIVATIVE,
            }
        }
        current_actual_value_by_key = {
            f"{item['member_type']}::{item['member_id']}": _safe_float(item.get("current_value_base"))
            for item in current_actual_rows
            if item.get("member_type") in {
                TARGET_MEMBER_NODE,
                TARGET_MEMBER_INSTRUMENT,
                TARGET_MEMBER_CASH,
                TARGET_MEMBER_DERIVATIVE,
            }
        }
    else:
        current_actual_rows = []
        current_actual_weight_by_key = {}
        current_actual_value_by_key = {}

    target_values = pd.Series(
        {
            f"{row['member_type']}::{row['member_id']}": float(_safe_float(row.get("selected_value")) or 0.0)
            for row in resolved_rows
        },
        dtype="float64",
    ).reindex(member_keys, fill_value=0.0)
    fixed_capital_keys = [
        key for key in member_keys if _member_is_fixed_capital(member_by_key[key])
    ]
    risk_bearing_keys = [key for key in member_keys if key not in fixed_capital_keys]
    if target_dimension_used == TARGET_DIMENSION_RISK_BUDGET and not risk_bearing_keys:
        raise ValueError(f"{scope_label} risk budget is unavailable: no risky members.")
    missing_frozen_actual_keys = [
        key for key in frozen_keys if key not in current_actual_weight_by_key
    ]
    if missing_frozen_actual_keys:
        missing_labels = ", ".join(
            member_by_key[key].label for key in missing_frozen_actual_keys
        )
        raise ValueError(
            f"{scope_label} cannot apply a no-trade constraint without as-of holdings: "
            f"{missing_labels}."
        )
    fixed_weight_targets = pd.Series(
        {
            key: current_actual_weight_by_key[key]
            for key in frozen_keys
        },
        dtype="float64",
    ).clip(lower=0.0)
    risk_keys = [
        key
        for key in member_keys
        if key not in fixed_capital_keys and key not in frozen_keys and key not in zero_target_keys
    ]
    solver_risk_keys = [
        key
        for key in member_keys
        if key not in fixed_capital_keys and key not in zero_target_keys
    ]
    preferred_fixed_capital_weights = pd.Series(
        {
            f"{row['member_type']}::{row['member_id']}": float(_safe_float(row.get("target_weight")) or 0.0)
            for row in resolved_rows
            if f"{row['member_type']}::{row['member_id']}" in fixed_capital_keys
        },
        dtype="float64",
    ).reindex(fixed_capital_keys, fill_value=0.0)
    derivative_key = (
        f"{TARGET_MEMBER_DERIVATIVE}::{SYSTEM_DERIVATIVE_TARGET_MEMBER_ID}"
    )
    if derivative_key in fixed_capital_keys:
        configured_derivative_weight = float(
            preferred_fixed_capital_weights.get(derivative_key, 0.0)
        )
        if fixed_derivative_weight_override is not None:
            frozen_derivative_weight = float(fixed_derivative_weight_override)
        elif include_actuals:
            frozen_derivative_weight = float(
                current_actual_weight_by_key.get(derivative_key, 0.0)
            )
        else:
            frozen_derivative_weight = 0.0
        if not np.isfinite(frozen_derivative_weight):
            raise ValueError("Derivative no-trade weight must be finite.")
        preferred_fixed_capital_weights.loc[derivative_key] = frozen_derivative_weight
        if abs(configured_derivative_weight - frozen_derivative_weight) > 1e-10:
            warnings.append(
                f"{scope_label} treats the configured Derivatives weight as informational only: "
                "FCN/options are no-trade, so actual signed carrying capital was preserved."
            )
    fixed_total = max(float(fixed_weight_targets.sum()), 0.0)
    overlay_applies_to_risk_sleeves = apply_capital_overlay and capital_mode in {
        CAPITAL_MODE_FIXED_GROSS,
        CAPITAL_MODE_TARGET_VOLATILITY,
        CAPITAL_MODE_VOLATILITY_CAP,
    }
    fixed_gross_overlay = overlay_applies_to_risk_sleeves and capital_mode == CAPITAL_MODE_FIXED_GROSS
    target_risk_bearing_total = float(gross_exposure or 1.0) if fixed_gross_overlay else None

    if target_dimension_used == TARGET_DIMENSION_RISK_BUDGET:
        base_fixed_capital_total = (
            0.0
            if fixed_gross_overlay
            else min(max(float(preferred_fixed_capital_weights.sum()), 0.0), 1.0)
        )
        available_risk_bearing_total = (
            float(target_risk_bearing_total)
            if target_risk_bearing_total is not None
            else max(1.0 - base_fixed_capital_total, 0.0)
        )
        if fixed_total > available_risk_bearing_total + 1e-12:
            raise ValueError(
                f"{scope_label} frozen sleeve weights require {fixed_total:.2%}, "
                f"above the available {available_risk_bearing_total:.2%} risk-bearing budget."
            )
        fixed_total = min(fixed_total, available_risk_bearing_total)
        _validate_fixed_top_sleeve_bounds(
            scope_label=scope_label,
            fixed_weight_targets=fixed_weight_targets,
            bounds_by_key=top_sleeve_bounds_by_key,
            member_by_key=member_by_key,
        )
        requested_active_budget = max(available_risk_bearing_total - fixed_total, 0.0)
        if risk_keys:
            active_budget, active_lower_bounds, active_upper_bounds = _resolve_active_top_sleeve_bound_vectors(
                scope_label=scope_label,
                active_keys=risk_keys,
                active_budget=requested_active_budget,
                bounds_by_key=top_sleeve_bounds_by_key,
                member_by_key=member_by_key,
                allow_upper_shortfall=not fixed_gross_overlay,
            )
        else:
            if fixed_gross_overlay and requested_active_budget > 1e-12:
                raise ValueError(
                    f"{scope_label} fixed gross requires {available_risk_bearing_total:.2%} risky exposure, "
                    f"but frozen sleeves supply only {fixed_total:.2%} and no sleeve is adjustable."
                )
            active_budget = 0.0
            active_lower_bounds = None
            active_upper_bounds = None

        solver_risk_total = fixed_total + active_budget
        if solver_risk_total <= 1e-12 or not solver_risk_keys:
            raise ValueError(f"{scope_label} risk budget is unavailable: no positive risk-bearing allocation.")

        lower_bounds = np.zeros(len(solver_risk_keys), dtype="float64")
        upper_bounds = np.ones(len(solver_risk_keys), dtype="float64")
        for index, key in enumerate(solver_risk_keys):
            if key in frozen_keys:
                fixed_local_weight = float(fixed_weight_targets.get(key, 0.0)) / solver_risk_total
                lower_bounds[index] = fixed_local_weight
                upper_bounds[index] = fixed_local_weight
                continue
            if active_lower_bounds is not None and active_upper_bounds is not None:
                active_index = risk_keys.index(key)
                lower_bounds[index] = float(active_lower_bounds[active_index]) * active_budget / solver_risk_total
                upper_bounds[index] = float(active_upper_bounds[active_index]) * active_budget / solver_risk_total

        if len(solver_risk_keys) > 1:
            solver_members = [member_by_key[key] for key in solver_risk_keys]
            return_window = _solver_return_window(
                members=solver_members,
                nav_series_by_member=nav_series_by_member,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
            )
        else:
            return_window = pd.DataFrame()
        risk_solve = _solve_risk_budget_weights(
            target_shares=target_values.reindex(solver_risk_keys, fill_value=0.0).to_numpy(dtype="float64"),
            return_window=return_window,
            reference_weights=None,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=calculation_frequency,
            missing_return_policy=missing_return_policy,
            risk_model_config=risk_model_config,
            lower_bounds=lower_bounds,
            upper_bounds=upper_bounds,
        )
        if not risk_solve.execution_ready:
            warnings.append(
                f"{scope_label} risk-budget target was not achieved under the configured constraints; "
                "the constrained result is for research comparison only and is not execution-ready."
            )
        elif risk_solve.target_status == "constrained_optimum":
            warnings.append(
                f"{scope_label} uses the verified optimum under hard no-trade/weight constraints; "
                f"the requested risk budget remains off by up to {float(risk_solve.max_abs_share_gap or 0.0):.2%}."
            )
        risk_gap = risk_solve.max_abs_share_gap
        solver_kind = risk_solve.solver_kind
        implementation_weights = pd.Series(0.0, index=member_keys, dtype="float64")
        implementation_weights.loc[solver_risk_keys] = risk_solve.weights * solver_risk_total
        implementation_weights.loc[fixed_capital_keys] = _allocate_fixed_capital_weights(
            member_index=fixed_capital_keys,
            preferred_weights=preferred_fixed_capital_weights,
            total_weight=1.0 - float(implementation_weights.loc[solver_risk_keys].sum()),
        )
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
        active_weight_keys = [
            key
            for key in member_keys
            if key not in fixed_capital_keys and key not in frozen_keys and key not in zero_target_keys
        ]
        active_weight_targets = target_values.reindex(active_weight_keys, fill_value=0.0)
        active_weight_total = float(active_weight_targets.sum())
        available_risk_bearing_total = (
            float(target_risk_bearing_total)
            if target_risk_bearing_total is not None
            else max(1.0 - float(preferred_fixed_capital_weights.sum()), 0.0)
        )
        if float(implementation_weights.loc[frozen_keys].sum()) > available_risk_bearing_total + 1e-12:
            raise ValueError(
                f"{scope_label} frozen sleeve weights require {float(implementation_weights.loc[frozen_keys].sum()):.2%}, "
                f"above the available {available_risk_bearing_total:.2%} risk-bearing budget."
            )
        requested_active_budget = max(
            available_risk_bearing_total - float(implementation_weights.loc[frozen_keys].sum()),
            0.0,
        )
        _validate_fixed_top_sleeve_bounds(
            scope_label=scope_label,
            fixed_weight_targets=fixed_weight_targets,
            bounds_by_key=top_sleeve_bounds_by_key,
            member_by_key=member_by_key,
        )
        if fixed_gross_overlay and not active_weight_keys and requested_active_budget > 1e-12:
            raise ValueError(
                f"{scope_label} fixed gross requires {available_risk_bearing_total:.2%} risky exposure, "
                f"but frozen sleeves supply only {float(implementation_weights.loc[frozen_keys].sum()):.2%} "
                "and no sleeve is adjustable."
            )
        active_budget, lower_bounds, upper_bounds = _resolve_active_top_sleeve_bound_vectors(
            scope_label=scope_label,
            active_keys=active_weight_keys,
            active_budget=requested_active_budget,
            bounds_by_key=top_sleeve_bounds_by_key,
            member_by_key=member_by_key,
            allow_upper_shortfall=not fixed_gross_overlay,
        )
        if active_weight_keys:
            if lower_bounds is not None and upper_bounds is not None:
                preferred = (
                    active_weight_targets.to_numpy(dtype="float64")
                    if active_weight_total > 1e-12
                    else np.ones(len(active_weight_keys), dtype="float64")
                )
                implementation_weights.loc[active_weight_keys] = _allocate_bounded_mass(
                    total_mass=active_budget,
                    lower=lower_bounds * active_budget,
                    upper=upper_bounds * active_budget,
                    preferred=preferred,
                )
            elif active_weight_total > 1e-12:
                implementation_weights.loc[active_weight_keys] = active_weight_targets / active_weight_total * active_budget
            else:
                implementation_weights.loc[active_weight_keys] = active_budget / float(len(active_weight_keys))
        implementation_weights.loc[fixed_capital_keys] = _allocate_fixed_capital_weights(
            member_index=fixed_capital_keys,
            preferred_weights=preferred_fixed_capital_weights,
            total_weight=1.0
            - float(implementation_weights.loc[active_weight_keys].sum())
            - float(implementation_weights.loc[frozen_keys].sum()),
        )
        risk_gap = None
        solver_kind = "weight-fixed-members" if frozen_keys else "weight"

    if (
        overlay_applies_to_risk_sleeves
        and capital_mode == CAPITAL_MODE_TARGET_VOLATILITY
        and risk_bearing_keys
        and not frozen_keys
    ):
        risk_bearing_total = float(implementation_weights.reindex(risk_bearing_keys, fill_value=0.0).sum())
        if risk_bearing_total <= 1e-12:
            raise ValueError(
                f"{scope_label} capital overlay is unavailable: no positive risky target weight."
            )
        implementation_weights.loc[risk_bearing_keys] = (
            implementation_weights.reindex(risk_bearing_keys, fill_value=0.0) / risk_bearing_total
        )
        implementation_weights.loc[fixed_capital_keys] = 0.0

    estimated_risk_sleeve_volatility = None
    effective_gross_exposure = None
    risky_allocation_scaling_factor = None
    if overlay_applies_to_risk_sleeves and risk_bearing_keys:
        risky_weights = implementation_weights.reindex(risk_bearing_keys, fill_value=0.0)
        if capital_mode == CAPITAL_MODE_FIXED_GROSS:
            effective_gross_exposure = float(risky_weights.sum())
            risky_allocation_scaling_factor = 1.0
        elif capital_mode in {CAPITAL_MODE_TARGET_VOLATILITY, CAPITAL_MODE_VOLATILITY_CAP}:
            active_risky_weights = risky_weights.loc[risky_weights.abs() > 1e-12]
            if active_risky_weights.empty:
                raise ValueError(
                    f"{scope_label} capital overlay is unavailable: no positive risky target weight."
                )
            solver_members = [member_by_key[key] for key in active_risky_weights.index]
            return_window = _solver_return_window(
                members=solver_members,
                nav_series_by_member=nav_series_by_member,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
            )
            estimated_risk_sleeve_volatility = _annualized_portfolio_volatility(
                return_window,
                active_risky_weights,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
                missing_return_policy=missing_return_policy,
                risk_model_config=risk_model_config,
            )
            try:
                if frozen_keys:
                    covariance = _estimate_covariance(
                        return_window,
                        model_id=_risk_model_covariance_model_id(risk_model_config),
                        lookback_days=lookback_days,
                        parameters=_risk_model_covariance_parameters(risk_model_config, calculation_frequency, lookback_days),
                        missing_return_policy=missing_return_policy,
                        calculation_frequency=calculation_frequency,
                        as_of_date=as_of_date,
                    )
                    frozen = active_risky_weights.reindex(covariance.index, fill_value=0.0)
                    frozen.loc[~frozen.index.isin(frozen_keys)] = 0.0
                    adjustable = active_risky_weights.reindex(covariance.index, fill_value=0.0) - frozen
                    matrix = covariance.to_numpy(dtype="float64")
                    fixed_vector = frozen.to_numpy(dtype="float64")
                    adjustable_vector = adjustable.to_numpy(dtype="float64")
                    gross_limit = (
                        float(risky_weights.sum())
                        if capital_mode == CAPITAL_MODE_VOLATILITY_CAP
                        else float(max_gross_exposure if max_gross_exposure is not None else 1.0)
                    )
                    adjustable_total = float(adjustable.sum())
                    if float(frozen.sum()) > gross_limit + 1e-12:
                        raise ValueError("Frozen sleeve weights exceed the permitted gross exposure.")
                    scale = max(gross_limit - float(frozen.sum()), 0.0) / adjustable_total if adjustable_total > 1e-12 else 0.0
                    a = float(adjustable_vector @ matrix @ adjustable_vector)
                    b = float(2.0 * fixed_vector @ matrix @ adjustable_vector)
                    c = float(fixed_vector @ matrix @ fixed_vector) - float(target_volatility or 0.0) ** 2
                    if a * scale * scale + b * scale + c > 1e-12:
                        discriminant = b * b - 4.0 * a * c
                        if a <= 1e-12 or discriminant < 0.0:
                            raise ValueError("Frozen sleeve weights make the volatility constraint infeasible.")
                        scale = min(scale, (-b + sqrt(discriminant)) / (2.0 * a))
                    if scale < 0.0 or a * scale * scale + b * scale + c > 1e-12:
                        raise ValueError("Frozen sleeve weights make the volatility constraint infeasible.")
                    achieved_variance = max(
                        a * scale * scale
                        + b * scale
                        + c
                        + float(target_volatility or 0.0) ** 2,
                        0.0,
                    )
                    achieved_volatility = sqrt(achieved_variance)
                    target_volatility_tolerance = max(
                        1e-10,
                        float(target_volatility or 0.0) * 1e-8,
                    )
                    if (
                        capital_mode == CAPITAL_MODE_TARGET_VOLATILITY
                        and abs(achieved_volatility - float(target_volatility or 0.0))
                        > target_volatility_tolerance
                    ):
                        raise ValueError(
                            "Target volatility cannot be reached with the frozen sleeve weights "
                            "and configured maximum gross exposure."
                        )
                    implementation_weights.loc[risk_bearing_keys] = (
                        frozen + adjustable * scale
                    ).reindex(risk_bearing_keys, fill_value=0.0)
                    effective_gross_exposure = float(implementation_weights.loc[risk_bearing_keys].sum())
                    risky_allocation_scaling_factor = scale
                else:
                    exposure_scale = _resolve_volatility_overlay_gross_exposure(
                        capital_mode=capital_mode,
                        estimated_volatility=estimated_risk_sleeve_volatility,
                        target_volatility=target_volatility,
                        max_gross_exposure=max_gross_exposure,
                    )
                    implementation_weights.loc[risk_bearing_keys] = risky_weights * exposure_scale
                    effective_gross_exposure = float(implementation_weights.loc[risk_bearing_keys].sum())
                    risky_allocation_scaling_factor = exposure_scale
            except ValueError as error:
                raise ValueError(
                    f"{scope_label} {str(error).removeprefix('Volatility overlay ')}"
                ) from error
        if effective_gross_exposure is not None and not fixed_gross_overlay:
            if fixed_capital_keys:
                implementation_weights.loc[fixed_capital_keys] = _allocate_fixed_capital_weights(
                    member_index=fixed_capital_keys,
                    preferred_weights=preferred_fixed_capital_weights,
                    total_weight=1.0
                    - float(implementation_weights.loc[risk_bearing_keys].sum()),
                )
            else:
                residual_weight = 1.0 - float(implementation_weights.loc[risk_bearing_keys].sum())
                if abs(residual_weight) > 1e-9:
                    raise ValueError(
                        f"{scope_label} capital overlay leaves a {residual_weight:.2%} residual but the scope has no fixed-capital member."
                    )

    _validate_final_top_sleeve_bounds(
        scope_label=scope_label,
        implementation_weights=implementation_weights,
        bounds_by_key=top_sleeve_bounds_by_key,
        member_by_key=member_by_key,
    )
    if fixed_gross_overlay and not fixed_capital_keys:
        residual_weight = 1.0 - float(implementation_weights.reindex(risk_bearing_keys, fill_value=0.0).sum())
        if abs(residual_weight) > 1e-9:
            raise ValueError(
                f"{scope_label} fixed gross leaves a {residual_weight:.2%} residual but the scope has no fixed-capital member."
            )

    for row in resolved_rows:
        member_key = f"{row['member_type']}::{row['member_id']}"
        row["implementation_weight"] = float(implementation_weights.get(member_key, 0.0))
    top_sleeve_bound_weight_by_id = {
        member_by_key[key].member_id: float(implementation_weights.get(key, 0.0))
        for key in top_sleeve_bounds_by_key
        if key in member_by_key
    }

    current_weights = pd.Series(current_actual_weight_by_key, dtype="float64").reindex(member_keys, fill_value=0.0)
    if include_actuals:
        current_risk_share_by_key, current_risk_share_warnings = _estimate_scope_risk_share_map(
            members=members,
            nav_series_by_member=current_nav_series_by_member,
            risk_keys=risk_bearing_keys,
            weights_by_key=current_weights,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=calculation_frequency,
            missing_return_policy=missing_return_policy,
            contribution_mode=_risk_model_contribution_mode(risk_model_config),
            risk_model_config=risk_model_config,
        )
        warnings.extend(current_risk_share_warnings)
    else:
        current_risk_share_by_key = {}
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
        "target_status": risk_solve.target_status,
        "execution_ready": risk_solve.execution_ready,
        "covariance_model": risk_solve.covariance_model,
        "covariance_observations": risk_solve.covariance_observations,
        "risk_contribution_mode": risk_solve.risk_contribution_mode,
        "missing_return_policy": risk_solve.missing_return_policy or _normalize_missing_return_policy(missing_return_policy),
        "return_rows_before_policy": risk_solve.return_rows_before_policy,
        "return_rows_after_policy": risk_solve.return_rows_after_policy,
        "missing_return_row_count": risk_solve.missing_return_row_count,
        "missing_return_row_fraction": risk_solve.missing_return_row_fraction,
        "leading_incomplete_return_row_count": risk_solve.leading_incomplete_return_row_count,
        "post_warmup_missing_return_row_count": risk_solve.post_warmup_missing_return_row_count,
        "post_warmup_missing_return_row_fraction": risk_solve.post_warmup_missing_return_row_fraction,
        "dropped_return_rows": deepcopy(risk_solve.dropped_return_rows or []),
        "latest_complete_return_date": risk_solve.latest_complete_return_date,
        "trailing_complete_return_staleness_days": risk_solve.trailing_complete_return_staleness_days,
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
        "no_trade_member_count": sum(
            1
            for member in members
            if _member_has_no_trade_constraint(
                state,
                scope_node_id=scope_node_id,
                member=member,
                inherited_no_trade=inherited_no_trade,
            )
        ),
        "risk_model_excluded_member_count": sum(
            1
            for member in members
            if _member_risk_model_status(member) == "excluded"
        ),
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
                "current_value_base": current_actual_value_by_key.get(member_key),
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
                "trade_constraint": (
                    "no_trade"
                    if _member_has_no_trade_constraint(
                        state,
                        scope_node_id=scope_node_id,
                        member=member,
                        inherited_no_trade=inherited_no_trade,
                    )
                    else "adjustable"
                ),
                "risk_model_status": _member_risk_model_status(member),
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
                        "current_value_base": child_leaf.get("current_value_base"),
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
                        "trade_constraint": child_leaf.get("trade_constraint"),
                        "risk_model_status": child_leaf.get("risk_model_status"),
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
                "current_value_base": current_actual_value_by_key.get(member_key),
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
                "trade_constraint": (
                    "no_trade"
                    if _member_has_no_trade_constraint(
                        state,
                        scope_node_id=scope_node_id,
                        member=member,
                        inherited_no_trade=inherited_no_trade,
                    )
                    else "adjustable"
                ),
                "risk_model_status": _member_risk_model_status(member),
            }
        )

    target_return_members = [
        member
        for member in members
        if abs(float(implementation_weights.get(f"{member.member_type}::{member.member_id}", 0.0))) > 1e-12
    ]
    try:
        scope_return_window = _solver_return_window(
            members=target_return_members,
            nav_series_by_member=nav_series_by_member,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=calculation_frequency,
        )
    except ValueError as error:
        scope_return_window = pd.DataFrame()
        warnings.append(f"{scope_label} target return series unavailable: {error}")
    if scope_return_window.empty:
        scope_returns = pd.Series(dtype="float64")
    else:
        scope_returns = _weighted_complete_return_series(scope_return_window, implementation_weights)
    if include_actuals:
        current_return_members = [
            member
            for member in members
            if abs(float(current_weights.get(f"{member.member_type}::{member.member_id}", 0.0))) > 1e-12
        ]
        try:
            current_scope_return_window = _solver_return_window(
                members=current_return_members,
                nav_series_by_member=current_nav_series_by_member,
                as_of_date=as_of_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
            )
        except ValueError as error:
            current_scope_return_window = pd.DataFrame()
            warnings.append(f"{scope_label} current return series unavailable: {error}")
    else:
        current_scope_return_window = pd.DataFrame()
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
        top_sleeve_bound_weight_by_id=top_sleeve_bound_weight_by_id,
    )


def _current_scope_actuals(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None,
    as_of_date: date,
) -> tuple[list[dict[str, object]], list[str]]:
    cache_key = f"actual-valuation::{as_of_date.isoformat()}"
    cached_valuation = state.current_valuation_cache.get(cache_key)
    if isinstance(cached_valuation, dict):
        position_value_by_instrument = dict(cached_valuation.get("position_value_by_instrument") or {})
        cash_value_by_account = dict(cached_valuation.get("cash_value_by_account") or {})
        excluded_derivative_contract_ids = list(
            cached_valuation.get("excluded_derivative_contract_ids") or []
        )
        derivative_total_value = float(
            _safe_float(cached_valuation.get("derivative_total_value")) or 0.0
        )
    else:
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
            instrument_detail_cache=state.instrument_detail_cache,
        )
        account_workspace = build_account_workspace(
            state.portfolio_id,
            accounts,
            transactions,
            base_currency=state.base_currency,
            as_of_date=as_of_date,
            instrument_detail_cache=state.instrument_detail_cache,
        )

        position_value_by_instrument: dict[str, float] = {}
        excluded_derivative_contract_ids: list[str] = []
        derivative_total_value = 0.0
        for position in list(statement.get("positions") or []):
            derivative_contract_id = str(
                position.get("derivative_contract_id") or ""
            )
            if holdings_market_profile.is_derivative_contract(
                position.get("derivative_contract")
            ):
                if derivative_contract_id:
                    excluded_derivative_contract_ids.append(
                        derivative_contract_id
                    )
                derivative_value_base = _safe_float(position.get("market_value_base"))
                if derivative_value_base is None:
                    raise ValueError(
                        "Current derivative carrying value is incomplete; refresh FX coverage before solving."
                    )
                derivative_total_value += derivative_value_base
                continue
            instrument_id = str(position.get("instrument_id") or "")
            if not instrument_id:
                continue
            market_value_base = _safe_float(position.get("market_value_base"))
            if market_value_base is None:
                raise ValueError(
                    "Current allocation valuation is incomplete; refresh price and FX coverage before solving."
                )
            position_value_by_instrument[instrument_id] = market_value_base

        cash_value_by_account: dict[str, float] = {}
        for account_row in list(account_workspace.get("accounts") or []):
            account = account_row.get("account") or {}
            account_id = str(account.get("account_id") or "")
            cash_balance_base = _safe_float(account_row.get("derived_cash_balance_base"))
            pending_settlement_base = _safe_float(account_row.get("pending_settlement_base"))
            if cash_balance_base is None or pending_settlement_base is None:
                raise ValueError(
                    "Current cash valuation is incomplete; refresh FX coverage before solving."
                )
            monetary_value_base = cash_balance_base + pending_settlement_base
            if account_id and (
                str(account.get("account_type") or "") == "deposit_account"
                or abs(monetary_value_base) > 1e-9
            ):
                cash_value_by_account[account_id] = monetary_value_base
        state.current_valuation_cache[cache_key] = {
            "position_value_by_instrument": dict(position_value_by_instrument),
            "cash_value_by_account": dict(cash_value_by_account),
            "excluded_derivative_contract_ids": sorted(
                set(excluded_derivative_contract_ids)
            ),
            "derivative_total_value": derivative_total_value,
        }

    cash_total_value = float(sum(cash_value_by_account.values()))
    direct_position_membership: dict[str, str] = {}
    for node_id, assignments in state.direct_assignments_by_node.items():
        for assignment in assignments:
            target_scope = str(assignment.get("target_scope") or "")
            target_entity_id = str(assignment.get("target_entity_id") or "")
            if target_scope == TARGET_MEMBER_INSTRUMENT:
                direct_position_membership[target_entity_id] = node_id

    node_value_map: dict[str, float] = {node_id: 0.0 for node_id in state.node_by_id}
    unassigned_value = 0.0
    unassigned_instrument_ids: list[str] = []

    for instrument_id, market_value_base in position_value_by_instrument.items():
        node_id = direct_position_membership.get(instrument_id)
        if not node_id:
            unassigned_value += market_value_base
            unassigned_instrument_ids.append(instrument_id)
            continue
        current_node_id = node_id
        while current_node_id:
            node_value_map[current_node_id] = float(node_value_map.get(current_node_id, 0.0) + market_value_base)
            current_node_id = str(state.node_by_id.get(current_node_id, {}).get("parent_taxonomy_node_id") or "") or None

    scope_members, member_source = _scope_members(state, scope_node_id=scope_node_id)
    scope_total_value = (
        sum(node_value_map.get(node_id, 0.0) for node_id in state.children_by_parent.get(None, []))
        + derivative_total_value
        + cash_total_value
        + unassigned_value
        if scope_node_id is None
        else node_value_map.get(scope_node_id, 0.0)
    )
    if abs(scope_total_value) <= 1e-9:
        scope_total_value = 0.0

    if scope_node_id is None and abs(unassigned_value) > 1e-9:
        rendered_ids = ", ".join(sorted(unassigned_instrument_ids))
        raise ValueError(
            "Research target solve requires complete planning-taxonomy coverage; "
            f"unassigned non-cash holdings: {rendered_ids}."
        )

    rendered_rows: list[dict[str, object]] = []
    warnings: list[str] = []
    if excluded_derivative_contract_ids:
        warnings.append(
            "Derivative holdings retain carrying-value weights but are excluded from the risk solve and Risk Budget: "
            + ", ".join(sorted(set(excluded_derivative_contract_ids)))
            + "."
        )
    for member in scope_members:
        if member.member_type == TARGET_MEMBER_NODE:
            actual_value = node_value_map.get(member.member_id, 0.0)
        elif member.member_type == TARGET_MEMBER_INSTRUMENT:
            actual_value = position_value_by_instrument.get(member.member_id, 0.0)
        elif (
            member.member_type == TARGET_MEMBER_DERIVATIVE
            and member.member_id == SYSTEM_DERIVATIVE_TARGET_MEMBER_ID
        ):
            actual_value = derivative_total_value
        elif member.member_type == TARGET_MEMBER_CASH and member.member_id == SYSTEM_CASH_TARGET_MEMBER_ID:
            actual_value = cash_total_value
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

    return rendered_rows, warnings


def _build_leaf_target_weight_gaps(
    *,
    leaf_target_rows: list[dict[str, object]],
    base_currency: str,
    scope_value_base: float | None = None,
    instrument_research_state_by_id: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    gaps: list[dict[str, object]] = []
    for row in leaf_target_rows:
        member_type = str(row.get("member_type") or "")
        member_id = str(row.get("member_id") or "")
        raw_research_state = (
            (instrument_research_state_by_id or {}).get(member_id)
            if member_type == TARGET_MEMBER_INSTRUMENT
            else None
        )
        research_state = (
            enrich_instrument_research_state(raw_research_state)
            if raw_research_state is not None
            else None
        )
        current_weight = _safe_float(row.get("current_weight"))
        target_weight = _safe_float(row.get("target_weight"))
        current_value_base = _safe_float(row.get("current_value_base"))
        target_value_base = (
            None
            if target_weight is None or scope_value_base is None
            else float(target_weight * scope_value_base)
        )
        gap = (
            None
            if current_weight is None or target_weight is None
            else float(target_weight - current_weight)
        )
        action = "Review"
        execution_status = "ready"
        execution_note = None
        trade_constraint = str(row.get("trade_constraint") or "adjustable")
        risk_model_status = str(row.get("risk_model_status") or "modeled")
        if trade_constraint == "no_trade":
            action = "No trade"
            execution_status = "no_trade"
            if current_value_base is not None:
                target_value_base = current_value_base
            execution_note = (
                "FCN/options are fixed at their actual carrying capital and excluded from covariance/Risk Budget; "
                "only recorded lifecycle events may change the position."
                if member_type == TARGET_MEMBER_DERIVATIVE
                else "This position is frozen at its as-of holding. It remains in the risk model, but Research cannot trade it."
            )
        elif (
            member_type == TARGET_MEMBER_INSTRUMENT
            and research_state is not None
            and research_state["research_lifecycle"] == "former"
            and research_state["research_eligibility"] == "pm_review_required"
            and target_weight is not None
            and target_weight > RESEARCH_EXECUTION_TARGET_EPSILON
        ):
            execution_status = "manual_review_required"
            execution_note = FORMER_PM_REVIEW_EXECUTION_NOTE
        elif gap is not None:
            if (
                member_type == TARGET_MEMBER_INSTRUMENT
                and current_weight > 1e-8
                and target_weight <= 1e-12
            ):
                action = "Review"
                execution_status = "manual_review_required"
                execution_note = (
                    "Current holdings with a 0% solved target require an explicit PM decision; "
                    "Research does not infer an executable liquidation from target eligibility or limited history."
                )
            elif gap > 0.01:
                action = "Increase"
            elif gap < -0.01:
                action = "Reduce"
            else:
                action = "Hold"
        gap_payload: dict[str, object] = {
            "member_type": row.get("member_type"),
            "member_id": row.get("member_id"),
            "label": row.get("label"),
            "current_weight": current_weight,
            "target_weight": target_weight,
            "gap": gap,
            "current_value_base": current_value_base,
            "target_value_base": target_value_base,
            "base_currency": base_currency,
            "action": action,
            "trade_constraint": trade_constraint,
            "risk_model_status": risk_model_status,
            "execution_status": execution_status,
            "execution_note": execution_note,
        }
        if research_state is not None:
            gap_payload.update(
                {
                    "research_lifecycle": research_state["research_lifecycle"],
                    "research_eligibility": research_state["research_eligibility"],
                    "research_pm_approved": research_state["research_pm_approved"],
                }
            )
        gaps.append(gap_payload)
    gaps.sort(key=lambda item: abs(_safe_float(item.get("gap")) or 0.0), reverse=True)
    return gaps


def _build_taxonomy_state(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    as_of_date: date,
    frozen_taxonomy_node_ids: list[str] | None = None,
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    direct_fx_instruments: dict[tuple[str, str], str] | None = None,
    require_planning_enabled: bool = True,
) -> TaxonomyResearchState:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")
    resolved_instrument_detail_cache = (
        instrument_detail_cache if instrument_detail_cache is not None else {}
    )

    configuration = taxonomy_configuration_as_of(
        portfolio_id,
        planning_taxonomy_id,
        as_of_date,
    )
    if configuration is None:
        raise ValueError(
            "No effective point-in-time taxonomy configuration exists for the selected date."
        )
    taxonomy = (
        configuration.get("taxonomy")
        if isinstance(configuration.get("taxonomy"), dict)
        else None
    )
    if taxonomy is None:
        raise ValueError("The effective taxonomy configuration is incomplete.")
    if str(taxonomy.get("status") or "") != "active":
        raise ValueError("The effective taxonomy configuration is inactive or deleted.")
    if require_planning_enabled and not bool(taxonomy.get("planning_enabled")):
        raise ValueError("The effective taxonomy configuration is not planning-enabled.")
    node_rows = [
        item
        for item in list(configuration.get("taxonomy_nodes") or [])
        if isinstance(item, dict) and str(item.get("status") or "") == "active"
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
        for item in list(configuration.get("taxonomy_assignments") or [])
        if isinstance(item, dict) and str(item.get("status") or "") == "active"
    ]
    active_target_ids = {str(item["target_set_id"]) for item in configuration.get("target_sets", [])
                         if isinstance(item, dict) and item.get("status") == "active"}
    explicit_members = {str(item["target_member_id"]) for item in configuration.get("target_set_lines", [])
                        if isinstance(item, dict) and item.get("target_member_type") == "instrument"
                        and str(item.get("target_set_id")) in active_target_ids}
    contract_only = contract_only_instrument_ids(portfolio_id, as_of_date=as_of_date,
                                                  explicitly_selected=explicit_members)
    assignments = [item for item in assignments if item.get("target_scope") != "instrument"
                   or item.get("target_entity_id") not in contract_only]
    assignments.sort(
        key=lambda item: (
            str(item.get("taxonomy_node_id") or ""),
            str(item.get("target_scope") or ""),
            str(item.get("target_entity_id") or ""),
        )
    )
    direct_assignments_by_node: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in assignments:
        node_id = str(assignment.get("taxonomy_node_id") or "")
        if node_id not in node_by_id:
            continue
        direct_assignments_by_node[node_id].append(assignment)

    target_sets = [
        item
        for item in list(configuration.get("target_sets") or [])
        if isinstance(item, dict) and str(item.get("status") or "") == "active"
    ]
    target_sets_by_scope_type: dict[tuple[str | None, str], list[dict[str, object]]] = defaultdict(list)
    for item in target_sets:
        comparator_node_id = str(item.get("comparator_taxonomy_node_id") or "") or None
        target_sets_by_scope_type[(comparator_node_id, str(item.get("target_set_type") or ""))].append(item)

    target_lines_by_set_id: dict[str, dict[tuple[str, str], dict[str, object]]] = defaultdict(dict)
    for line in list(configuration.get("target_set_lines") or []):
        if not isinstance(line, dict):
            continue
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
        base_currency=valuation_fx.required_currency(
            portfolio.get("base_currency"), field_name="portfolio base currency"
        ),
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
        instrument_detail_cache=resolved_instrument_detail_cache,
        direct_fx_instruments=(
            direct_fx_instruments
            if direct_fx_instruments is not None
            else valuation_fx.fx_direct_instrument_map(get_shared_fx_rates())
        ),
        frozen_taxonomy_node_ids=frozenset(
            str(item).strip() for item in (frozen_taxonomy_node_ids or []) if str(item).strip()
        ),
        top_sleeve_weight_bounds=_normalize_top_sleeve_weight_bounds(top_sleeve_weight_bounds),
        configuration_version=(
            int(configuration["configuration_version"])
            if configuration.get("configuration_version") is not None
            else None
        ),
        configuration_effective_from=_parse_iso_date(
            configuration.get("effective_from")
        ),
        instrument_analytics_scopes=resolve_instrument_analytics_scopes(
            portfolio_id,
            taxonomy_id=planning_taxonomy_id,
            as_of_date=as_of_date,
            instrument_ids=[
                str(item.get("target_entity_id") or "")
                for item in assignments
                if item.get("target_scope") == TARGET_MEMBER_INSTRUMENT
            ],
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
        require_planning_enabled=False,
    )
    options: list[dict[str, object]] = [
        {
            "taxonomy_node_id": None,
            "label": ROOT_SCOPE_LABEL,
            "path": ROOT_SCOPE_LABEL,
            "depth": 0,
            "default_target_dimension": state.root_default_target_dimension,
            "has_children": any(
                _node_has_research_members(state, node_id)
                for node_id in state.children_by_parent.get(None, [])
            ),
        }
    ]
    for node_id in state.node_by_id:
        if not _node_has_research_members(state, node_id):
            continue
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
    _instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    _direct_fx_instruments: dict[tuple[str, str], str] | None = None,
) -> dict[str, object]:
    if not planning_taxonomy_id:
        return calculation_frequency_profile(instrument_count=0)
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        instrument_detail_cache=_instrument_detail_cache,
        direct_fx_instruments=_direct_fx_instruments,
        require_planning_enabled=False,
    )
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    scope_node_ids = (
        set(state.node_by_id)
        if comparator_taxonomy_node_id is None
        else state.node_subtree_by_id.get(
            comparator_taxonomy_node_id,
            {comparator_taxonomy_node_id},
        )
    )
    scope_instrument_ids = sorted(
        {
            str(assignment.get("target_entity_id") or "").strip()
            for node_id in scope_node_ids
            for assignment in state.direct_assignments_by_node.get(node_id, [])
            if str(assignment.get("target_scope") or "")
            == TARGET_MEMBER_INSTRUMENT
            and str(assignment.get("target_entity_id") or "").strip()
        }
    )
    missing_instrument_ids = [
        instrument_id
        for instrument_id in scope_instrument_ids
        if instrument_id not in state.instrument_detail_cache
    ]
    if missing_instrument_ids:
        state.instrument_detail_cache.update(
            get_registry_instrument_details(missing_instrument_ids)
        )
    start_day = research_window_start_date(as_of_date, lookback_days)
    instrument_count = _scope_instrument_count(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        start_date=start_day,
        end_date=as_of_date,
    )
    return calculation_frequency_profile(
        instrument_count=instrument_count,
    )


def _top_sleeve_for_member(
    state: TaxonomyResearchState,
    *,
    member_type: str,
    member_id: str,
) -> tuple[str | None, str, str]:
    if member_type == TARGET_MEMBER_DERIVATIVE:
        return (
            SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
            SYSTEM_DERIVATIVE_TARGET_LABEL,
            SYSTEM_DERIVATIVE_TARGET_LABEL,
        )
    if member_type == TARGET_MEMBER_CASH:
        return SYSTEM_CASH_TARGET_MEMBER_ID, SYSTEM_CASH_TARGET_LABEL, SYSTEM_CASH_TARGET_LABEL

    node_id: str | None = None
    if member_type == TARGET_MEMBER_NODE and member_id in state.node_by_id:
        node_id = member_id
    else:
        for candidate_node_id, assignments in state.direct_assignments_by_node.items():
            if any(
                str(assignment.get("target_scope") or "") == member_type
                and str(assignment.get("target_entity_id") or "") == member_id
                for assignment in assignments
            ):
                node_id = candidate_node_id
                break
    if not node_id or node_id not in state.node_by_id:
        return None, "Unassigned", "Unassigned"
    current_id = node_id
    while True:
        parent_id = str(state.node_by_id.get(current_id, {}).get("parent_taxonomy_node_id") or "") or None
        if parent_id is None or parent_id not in state.node_by_id:
            break
        current_id = parent_id
    node = state.node_by_id[current_id]
    label = str(node.get("node_name") or current_id)
    return current_id, label, state.node_path_by_id.get(current_id, label)


def _target_key(row: dict[str, object]) -> str:
    return f"{row.get('member_type')}::{row.get('member_id')}"


def _top_sleeve_bound_status(
    *,
    solved_weight: float | None,
    min_weight: float | None,
    max_weight: float | None,
) -> str | None:
    if min_weight is None and max_weight is None:
        return None
    if solved_weight is None:
        return "missing"
    tolerance = 1e-6
    if min_weight is not None and solved_weight < min_weight - tolerance:
        return "violated"
    if max_weight is not None and solved_weight > max_weight + tolerance:
        return "violated"
    if min_weight is not None and abs(solved_weight - min_weight) <= tolerance:
        return "min"
    if max_weight is not None and abs(solved_weight - max_weight) <= tolerance:
        return "max"
    return "within"


def _estimate_forward_risk_contribution_by_key(
    state: TaxonomyResearchState,
    *,
    leaf_rows: list[dict[str, object]],
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
    missing_return_policy: str,
    risk_model_config: dict[str, object] | None,
) -> tuple[dict[str, float | None], list[str]]:
    risk_rows = [
        row
        for row in leaf_rows
        if str(row.get("member_type") or "") == TARGET_MEMBER_INSTRUMENT
        and abs(_safe_float(row.get("target_weight")) or 0.0) > 1e-12
    ]
    if len(risk_rows) == 1:
        return ({_target_key(risk_rows[0]): 1.0}, [])

    start_day = research_window_start_date(as_of_date, lookback_days)
    members: list[ScopeMemberRecord] = []
    nav_series_by_member: dict[tuple[str, str], pd.Series] = {}
    usable_rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for row in risk_rows:
        instrument_id = str(row.get("member_id") or "")
        try:
            nav_series, row_warnings = _build_instrument_nav_series(
                state,
                instrument_id=instrument_id,
                start_date=start_day,
                end_date=as_of_date,
            )
        except ValueError as error:
            warnings.append(f"{row.get('label') or instrument_id} forward RC unavailable: {error}")
            continue
        member = ScopeMemberRecord(
            member_type=TARGET_MEMBER_INSTRUMENT,
            member_id=instrument_id,
            label=str(row.get("label") or instrument_id),
        )
        members.append(member)
        nav_series_by_member[(member.member_type, member.member_id)] = nav_series
        usable_rows.append(row)
        warnings.extend(row_warnings)

    if len(usable_rows) == 1:
        return ({_target_key(usable_rows[0]): 1.0}, list(dict.fromkeys(warnings)))
    if not usable_rows:
        return {}, list(dict.fromkeys(warnings))

    try:
        return_window = _solver_return_window(
            members=members,
            nav_series_by_member=nav_series_by_member,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=calculation_frequency,
        )
        covariance_parameters = _risk_model_covariance_parameters(risk_model_config, calculation_frequency, lookback_days)
        covariance = _estimate_covariance(
            return_window,
            model_id=_risk_model_covariance_model_id(risk_model_config),
            lookback_days=lookback_days,
            parameters=covariance_parameters,
            missing_return_policy=_normalize_missing_return_policy(missing_return_policy),
            calculation_frequency=calculation_frequency,
            as_of_date=as_of_date,
        )
        weights = np.asarray([_safe_float(row.get("target_weight")) or 0.0 for row in usable_rows], dtype="float64")
        shares = _risk_contribution_shares(
            covariance.to_numpy(dtype="float64"),
            weights,
            contribution_mode=_risk_model_contribution_mode(risk_model_config),
        )
    except ValueError as error:
        warnings.append(f"Forward RC unavailable: {error}")
        return ({_target_key(row): None for row in usable_rows}, list(dict.fromkeys(warnings)))

    return (
        {_target_key(row): float(shares[index]) for index, row in enumerate(usable_rows)},
        list(dict.fromkeys(warnings)),
    )


def _selected_target_risk_share(row: dict[str, object]) -> float | None:
    if str(row.get("selected_target_dimension") or "") != TARGET_DIMENSION_RISK_BUDGET:
        return None
    return _safe_float(row.get("configured_risk_share"))


def _build_solved_result_groups(
    state: TaxonomyResearchState,
    *,
    leaf_rows: list[dict[str, object]],
    member_rows: list[dict[str, object]],
    scope_value_base: float | None,
    top_sleeve_bound_weight_by_id: dict[str, float] | None = None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: CalculationFrequency,
    missing_return_policy: str,
    risk_model_config: dict[str, object] | None,
) -> tuple[list[dict[str, object]], list[str]]:
    forward_rc_by_key, warnings = _estimate_forward_risk_contribution_by_key(
        state,
        leaf_rows=leaf_rows,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
        missing_return_policy=missing_return_policy,
        risk_model_config=risk_model_config,
    )
    group_target_risk_by_id = {
        str(row.get("member_id") or ""): _selected_target_risk_share(row)
        for row in member_rows
        if str(row.get("member_type") or "") == TARGET_MEMBER_NODE
    }
    groups: dict[str, dict[str, object]] = {}
    for leaf in leaf_rows:
        member_type = str(leaf.get("member_type") or "")
        member_id = str(leaf.get("member_id") or "")
        top_sleeve_id, top_sleeve_label, _path = _top_sleeve_for_member(
            state,
            member_type=member_type,
            member_id=member_id,
        )
        group_key = top_sleeve_id or "__unassigned__"
        group_bounds = state.top_sleeve_weight_bounds.get(top_sleeve_id or "")
        group = groups.setdefault(
            group_key,
            {
                "top_sleeve_id": top_sleeve_id,
                "top_sleeve_label": top_sleeve_label,
                "current_weight": 0.0,
                "solved_weight": 0.0,
                "current_value_base": 0.0,
                "target_value_base": 0.0 if scope_value_base is not None else None,
                "target_risk_share": group_target_risk_by_id.get(top_sleeve_id or ""),
                "forward_risk_contribution": 0.0,
                "min_weight": _safe_float((group_bounds or {}).get("min_weight")),
                "max_weight": _safe_float((group_bounds or {}).get("max_weight")),
                "bound_status": None,
                "trade_constraint": "adjustable",
                "risk_model_status": "modeled",
                "rows": [],
            },
        )
        current_weight = _safe_float(leaf.get("current_weight"))
        solved_weight = _safe_float(leaf.get("target_weight"))
        current_value_base = _safe_float(leaf.get("current_value_base"))
        target_value_base = (
            None
            if solved_weight is None or scope_value_base is None
            else float(solved_weight * scope_value_base)
        )
        forward_rc = forward_rc_by_key.get(_target_key(leaf))
        row = {
            "member_type": member_type,
            "member_id": member_id,
            "label": str(leaf.get("label") or member_id),
            "top_sleeve_id": top_sleeve_id,
            "top_sleeve_label": top_sleeve_label,
            "current_weight": current_weight,
            "solved_weight": solved_weight,
            "current_value_base": current_value_base,
            "target_value_base": target_value_base,
            "target_risk_share": _selected_target_risk_share(leaf),
            "forward_risk_contribution": forward_rc,
            "trade_constraint": str(
                leaf.get("trade_constraint") or "adjustable"
            ),
            "risk_model_status": str(
                leaf.get("risk_model_status") or "modeled"
            ),
        }
        group["rows"].append(row)
        group["current_weight"] = float(group["current_weight"] or 0.0) + float(current_weight or 0.0)
        group["solved_weight"] = float(group["solved_weight"] or 0.0) + float(solved_weight or 0.0)
        group["current_value_base"] = float(group["current_value_base"] or 0.0) + float(current_value_base or 0.0)
        if target_value_base is not None:
            group["target_value_base"] = float(group["target_value_base"] or 0.0) + target_value_base
        if forward_rc is not None:
            group["forward_risk_contribution"] = float(group["forward_risk_contribution"] or 0.0) + float(forward_rc)

    rendered = list(groups.values())
    for group in rendered:
        rows = list(group.get("rows") or [])
        rows.sort(key=lambda item: abs(_safe_float(item.get("solved_weight")) or 0.0), reverse=True)
        group["rows"] = rows
        trade_constraints = {
            str(item.get("trade_constraint") or "adjustable") for item in rows
        }
        risk_model_statuses = {
            str(item.get("risk_model_status") or "modeled") for item in rows
        }
        group["trade_constraint"] = (
            next(iter(trade_constraints))
            if len(trade_constraints) == 1
            else "mixed"
        )
        group["risk_model_status"] = (
            next(iter(risk_model_statuses))
            if len(risk_model_statuses) == 1
            else "mixed"
        )
        if abs(float(group.get("forward_risk_contribution") or 0.0)) <= 1e-12:
            group["forward_risk_contribution"] = None
        bound_status_weight = _safe_float(
            (top_sleeve_bound_weight_by_id or {}).get(str(group.get("top_sleeve_id") or ""))
        )
        group["bound_status"] = _top_sleeve_bound_status(
            solved_weight=bound_status_weight
            if bound_status_weight is not None
            else _safe_float(group.get("solved_weight")),
            min_weight=_safe_float(group.get("min_weight")),
            max_weight=_safe_float(group.get("max_weight")),
        )
    rendered.sort(key=lambda item: abs(_safe_float(item.get("solved_weight")) or 0.0), reverse=True)
    return rendered, warnings


def _normalize_backtest_rebalance_frequency(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"1w", "1m", "3m"} else "1m"


def _rebalance_schedule(*, start_date: date, end_date: date, frequency: str) -> list[date]:
    normalized_frequency = _normalize_backtest_rebalance_frequency(frequency)
    if normalized_frequency == "1w":
        dates: list[date] = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current = current + timedelta(days=7)
        return dates

    month_step = 3 if normalized_frequency == "3m" else 1
    first_month = date(start_date.year, start_date.month, 1)
    if first_month < start_date:
        first_month = (pd.Timestamp(first_month) + pd.DateOffset(months=1)).date()
    # Initialize at policy inception even if the first regular month boundary
    # is later. Extending the cutoff must not remove an existing first month.
    dates: list[date] = [start_date] if start_date <= end_date and first_month > start_date else []
    current = first_month
    while current <= end_date:
        dates.append(current)
        current = (pd.Timestamp(current) + pd.DateOffset(months=month_step)).date()
    return dates


def _nav_returns(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="float64")
    cleaned = series.sort_index().astype("float64")
    return cleaned.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna()


def _compound_return(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    for value in values:
        total *= 1.0 + float(value)
    return total - 1.0


def _build_backtest_metrics(points: list[dict[str, object]], returns: dict[str, float]) -> dict[str, object]:
    parsed_points = sorted(
        [
            (parsed_date, value)
            for item in points
            if (parsed_date := _parse_iso_date(item.get("date"))) is not None
            and (value := _safe_float(item.get("value"))) is not None
        ],
        key=lambda item: item[0],
    )
    start_date = parsed_points[0][0] if parsed_points else None
    end_date = parsed_points[-1][0] if parsed_points else None
    annualization = annualization_eligibility(start_date, end_date)
    if len(parsed_points) < 2 or not returns:
        return {
            "start_date": points[0]["date"] if points else None,
            "end_date": points[-1]["date"] if points else None,
            "period_return": None,
            "ytd_return": None,
            "annualization_eligible": annualization.eligible,
            "annualization_years": annualization.years,
            "annualization_unavailable_reason": annualization.unavailable_reason,
            "annualized_return": None,
            "annualized_volatility": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
            "max_drawdown_start_date": None,
            "max_drawdown_end_date": None,
            "max_drawdown_days": None,
            "max_drawdown_recovery_date": None,
            "max_drawdown_recovery_days": None,
            "current_drawdown": None,
            "calmar_ratio": None,
        }
    start_value = parsed_points[0][1]
    end_value = parsed_points[-1][1]
    dates = [item[0] for item in parsed_points]
    return_dates = [_parse_iso_date(date_key) for date_key in returns.keys()]
    periods_per_year = _infer_periods_per_year([item for item in return_dates if item is not None])
    period_return = end_value / start_value - 1.0 if abs(start_value) > 1e-12 else None
    end_date = dates[-1] if dates else None
    year_start = date(end_date.year, 1, 1) if end_date is not None else None
    # A YTD base must represent the year boundary, not merely be any older NAV
    # point.  Seven days accommodates normal year-end market closures across
    # daily backtests without relabelling
    # a stale multi-period return as YTD.
    earliest_ytd_anchor = year_start - timedelta(days=7) if year_start is not None else None
    ytd_anchor = next(
        (
            value
            for point_date, value in reversed(parsed_points)
            if year_start is not None
            and earliest_ytd_anchor is not None
            and earliest_ytd_anchor <= point_date <= year_start
        ),
        None,
    )
    ytd_return = (
        end_value / ytd_anchor - 1.0
        if ytd_anchor is not None and abs(ytd_anchor) > 1e-12
        else None
    )
    annualized_return = (
        (end_value / start_value) ** (1.0 / annualization.years) - 1.0
        if annualization.eligible
        and annualization.years is not None
        and period_return is not None
        and end_value > 0
        and start_value > 0
        else None
    )
    return_values = np.asarray(list(returns.values()), dtype="float64")
    annualized_volatility = (
        float(np.nanstd(return_values, ddof=1) * sqrt(periods_per_year)) if len(return_values) > 1 else None
    )
    annualized_arithmetic_mean_return = (
        float(np.nanmean(return_values) * periods_per_year) if len(return_values) > 0 else None
    )
    sharpe_ratio = (
        float(annualized_arithmetic_mean_return / annualized_volatility)
        if annualized_arithmetic_mean_return is not None
        and annualized_volatility is not None
        and annualized_volatility > 1e-12
        else None
    )

    high_value = -np.inf
    high_date: date | None = None
    max_drawdown = 0.0
    max_start: date | None = None
    max_end: date | None = None
    recovery_date: date | None = None
    target_recovery_value: float | None = None
    for point in points:
        point_date = _parse_iso_date(point.get("date"))
        point_value = _safe_float(point.get("value"))
        if point_date is None or point_value is None:
            continue
        if point_value > high_value:
            high_value = point_value
            high_date = point_date
        drawdown = point_value / high_value - 1.0 if high_value > 0 else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_start = high_date
            max_end = point_date
            recovery_date = None
            target_recovery_value = high_value
        if recovery_date is None and max_end is not None and point_date > max_end and target_recovery_value is not None:
            if point_value >= target_recovery_value:
                recovery_date = point_date
    max_drawdown_days = (max_end - max_start).days if max_start is not None and max_end is not None else None
    recovery_days = (recovery_date - max_end).days if recovery_date is not None and max_end is not None else None
    current_drawdown = end_value / high_value - 1.0 if high_value > 0 and end_value is not None else None
    calmar_ratio = (
        float(annualized_return / abs(max_drawdown))
        if annualized_return is not None and max_drawdown is not None and max_drawdown < -1e-12
        else None
    )
    return {
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "period_return": period_return,
        "ytd_return": ytd_return,
        "annualization_eligible": annualization.eligible,
        "annualization_years": annualization.years,
        "annualization_unavailable_reason": annualization.unavailable_reason,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "max_drawdown_start_date": max_start.isoformat() if max_start else None,
        "max_drawdown_end_date": max_end.isoformat() if max_end else None,
        "max_drawdown_days": max_drawdown_days,
        "max_drawdown_recovery_date": recovery_date.isoformat() if recovery_date else None,
        "max_drawdown_recovery_days": recovery_days,
        "current_drawdown": current_drawdown,
        "calmar_ratio": calmar_ratio,
    }


def _is_rebalance_data_gap_error(error: ValueError) -> bool:
    message = str(error)
    return (
        "requires at least" in message
        or "return observations" in message
        or "complete aligned return observations" in message
        or "complete-case drop would remove" in message
        or "has no active complete" in message
        or "does not have usable market history" in message
    )


def _instrument_label(state: TaxonomyResearchState, instrument_id: str) -> str:
    detail = _instrument_detail(state, instrument_id)
    if isinstance(detail, dict):
        return str(detail.get("instrument_name") or instrument_id)
    return instrument_id


def _normalized_backtest_points(points: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized_points: list[dict[str, object]] = []
    for point in points:
        point_date = _parse_iso_date(point.get("date"))
        point_value = _safe_float(point.get("value"))
        if point_date is None or point_value is None:
            continue
        normalized_points.append({"date": point_date.isoformat(), "value": float(point_value)})
    normalized_points.sort(key=lambda item: str(item.get("date") or ""))
    return normalized_points


def _backtest_return_map_from_points(points: list[dict[str, object]]) -> dict[str, float]:
    normalized_points = _normalized_backtest_points(points)
    if len(normalized_points) < 2:
        return {}
    returns: dict[str, float] = {}
    previous_value = _safe_float(normalized_points[0].get("value"))
    for point in normalized_points[1:]:
        point_value = _safe_float(point.get("value"))
        date_key = str(point.get("date") or "")
        if previous_value is None or point_value is None or previous_value <= 0.0:
            previous_value = point_value
            continue
        returns[date_key] = point_value / previous_value - 1.0
        previous_value = point_value
    return returns


def rebuild_backtest_metrics_from_points(points: list[dict[str, object]]) -> dict[str, object]:
    """Rebuild persisted metrics from their canonical NAV points.

    Research runs are durable snapshots, but metric definitions can be corrected
    over time.  The point series is the source of truth, so read paths can safely
    refresh derived metrics without rewriting the historical run.
    """
    normalized_points = _normalized_backtest_points(points)
    return _build_backtest_metrics(
        normalized_points,
        _backtest_return_map_from_points(normalized_points),
    )


def _build_backtest_sampled_nav_by_instrument(
    nav_by_instrument: dict[str, pd.Series],
    *,
    end_date: date,
) -> dict[str, pd.Series]:
    sampled: dict[str, pd.Series] = {}
    for instrument_id, series in nav_by_instrument.items():
        sampled_series = _periodic_nav_series(
            series,
            calculation_frequency="daily",
            start_date=date(1900, 1, 1),
            end_date=end_date,
        )
        if not sampled_series.empty:
            sampled[instrument_id] = sampled_series
    return sampled


def _backtest_rebalance_dates(
    *,
    start_date: date,
    end_date: date,
    frequency: str,
) -> list[date]:
    return _rebalance_schedule(start_date=start_date, end_date=end_date, frequency=frequency)


def _latest_series_value_on_or_before(series: pd.Series, point_date: date) -> float | None:
    if series.empty:
        return None
    eligible = series.loc[series.index <= point_date]
    if eligible.empty:
        return None
    value = _safe_float(eligible.iloc[-1])
    if value is None or value <= 0.0:
        return None
    return float(value)


def _build_sampled_benchmark_points(
    benchmark_nav: pd.Series,
    normalized_points: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, float]]:
    benchmark_points: list[dict[str, object]] = []
    benchmark_return_map: dict[str, float] = {}
    if not normalized_points or benchmark_nav.empty:
        return benchmark_points, benchmark_return_map

    benchmark_nav_value = 1.0
    previous_benchmark_value: float | None = None
    for point in normalized_points:
        date_key = str(point.get("date") or "")
        point_date = _parse_iso_date(date_key)
        if point_date is None:
            continue
        benchmark_value = _latest_series_value_on_or_before(benchmark_nav, point_date)
        if benchmark_value is None:
            continue
        if previous_benchmark_value is None:
            benchmark_points.append({"date": date_key, "value": benchmark_nav_value})
            previous_benchmark_value = benchmark_value
            continue
        benchmark_return = benchmark_value / previous_benchmark_value - 1.0
        benchmark_return_map[date_key] = benchmark_return
        benchmark_nav_value *= 1.0 + benchmark_return
        benchmark_points.append({"date": date_key, "value": benchmark_nav_value})
        previous_benchmark_value = benchmark_value

    return benchmark_points, benchmark_return_map


def _build_backtest_benchmark_comparison_from_state(
    state: TaxonomyResearchState,
    *,
    benchmark_instrument_id: str | None,
    portfolio_points: list[dict[str, object]],
    portfolio_returns: dict[str, float] | None = None,
) -> dict[str, object]:
    normalized_benchmark_id = str(benchmark_instrument_id or "").strip()
    if not normalized_benchmark_id:
        return {"backtest_benchmark": None, "backtest_relative_metrics": None}

    normalized_points = _normalized_backtest_points(portfolio_points)
    portfolio_return_map = portfolio_returns or _backtest_return_map_from_points(normalized_points)
    benchmark_label = _instrument_label(state, normalized_benchmark_id)
    benchmark_warnings: list[str] = []
    benchmark_detail = _instrument_detail(state, normalized_benchmark_id)
    benchmark_candidate_bases = (
        benchmark_total_return_quote_bases(benchmark_detail)
        if isinstance(benchmark_detail, dict)
        else []
    )
    benchmark_basis_warning: str | None = None
    if isinstance(benchmark_detail, dict) and not benchmark_candidate_bases:
        instrument_type = str(
            benchmark_detail.get("instrument_type") or ""
        ).strip().lower()
        source_settings = benchmark_detail.get("source_settings")
        configured_semantics = (
            str(source_settings.get("return_semantics") or "unknown")
            .strip()
            .lower()
            if isinstance(source_settings, dict)
            else "unknown"
        )
        price_comparison_confirmed = (
            instrument_type != "index" or configured_semantics == "price_return"
        )
        if price_comparison_confirmed:
            benchmark_candidate_bases = analytical_return_quote_bases(
                benchmark_detail
            )
            if benchmark_candidate_bases:
                benchmark_basis_warning = (
                    f"{normalized_benchmark_id} uses a price-return series. "
                    "Portfolio returns include income, so excess return and relative "
                    "statistics include that basis difference."
                )
    if not benchmark_candidate_bases:
        benchmark_warnings.append(
            f"{normalized_benchmark_id} does not have a confirmed total-return series; "
            "portfolio-relative research metrics are unavailable."
        )
        benchmark_nav = pd.Series(dtype="float64")
    else:
        try:
            benchmark_nav, instrument_warnings = _build_instrument_nav_series(
                state,
                instrument_id=normalized_benchmark_id,
                start_date=date(1900, 1, 1),
                end_date=state.as_of_date,
                warn_on_start_clip=False,
                candidate_bases=benchmark_candidate_bases,
            )
            benchmark_warnings.extend(instrument_warnings)
            if benchmark_basis_warning is not None:
                benchmark_warnings.append(benchmark_basis_warning)
        except ValueError as error:
            benchmark_warnings.append(str(error))
            benchmark_nav = pd.Series(dtype="float64")

    benchmark_points, benchmark_return_map = _build_sampled_benchmark_points(benchmark_nav, normalized_points)

    benchmark_payload = {
        "instrument_id": normalized_benchmark_id,
        "label": benchmark_label or normalized_benchmark_id,
        "points": benchmark_points,
        "metrics": _build_backtest_metrics(benchmark_points, benchmark_return_map),
        "warnings": benchmark_warnings,
    }

    relative_metrics = None
    common_return_dates = sorted(set(portfolio_return_map).intersection(benchmark_return_map))
    if common_return_dates:
        active_points: list[dict[str, object]] = []
        active_return_map: dict[str, float] = {}
        active_nav_value = 1.0
        if normalized_points:
            active_points.append({"date": normalized_points[0]["date"], "value": active_nav_value})
        for date_key in common_return_dates:
            active_return = float(portfolio_return_map[date_key]) - float(benchmark_return_map[date_key])
            active_return_map[date_key] = active_return
            active_nav_value *= 1.0 + active_return
            active_points.append({"date": date_key, "value": active_nav_value})
        active_metrics = _build_backtest_metrics(active_points, active_return_map)
        portfolio_common = [float(portfolio_return_map[date_key]) for date_key in common_return_dates]
        benchmark_common = [float(benchmark_return_map[date_key]) for date_key in common_return_dates]
        portfolio_period_return = _compound_return(portfolio_common)
        benchmark_period_return = _compound_return(benchmark_common)
        tracking_error = _safe_float(active_metrics.get("annualized_volatility"))
        parsed_dates = [_parse_iso_date(item) for item in common_return_dates]
        periods_per_year = _infer_periods_per_year([item for item in parsed_dates if item is not None])
        information_ratio = (
            float((np.mean(list(active_return_map.values())) * periods_per_year) / tracking_error)
            if tracking_error is not None and tracking_error > 1e-12
            else None
        )
        relative_metrics = {
            **active_metrics,
            "excess_return": (
                portfolio_period_return - benchmark_period_return
                if portfolio_period_return is not None and benchmark_period_return is not None
                else None
            ),
            "tracking_error": tracking_error,
            "information_ratio": information_ratio,
        }

    return {"backtest_benchmark": benchmark_payload, "backtest_relative_metrics": relative_metrics}


def build_research_backtest_benchmark_comparison(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    as_of_date: date,
    benchmark_instrument_id: str,
    portfolio_points: list[dict[str, object]],
) -> dict[str, object]:
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    return _build_backtest_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id=benchmark_instrument_id,
        portfolio_points=portfolio_points,
    )


def _historical_backtest_instrument_ids(
    revisions: list[dict[str, object]],
    *,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
) -> list[str]:
    instrument_ids: set[str] = set()
    for revision in revisions:
        for assignment in list(revision.get("taxonomy_assignments") or []):
            if not isinstance(assignment, dict):
                continue
            if str(assignment.get("status") or "") != "active":
                continue
            if str(assignment.get("target_scope") or "") != TARGET_MEMBER_INSTRUMENT:
                continue
            instrument_id = str(assignment.get("target_entity_id") or "").strip()
            if not instrument_id:
                continue
            instrument_ids.add(instrument_id)
    return sorted(instrument_ids)


def _backtest_target_weights(
    solution: dict[str, object],
) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in list(solution.get("leaf_targets") or []):
        if str(row.get("member_type") or "") != TARGET_MEMBER_INSTRUMENT:
            continue
        instrument_id = str(row.get("member_id") or "").strip()
        weight = float(_safe_float(row.get("target_weight")) or 0.0)
        if instrument_id and abs(weight) > 1e-12:
            weights[instrument_id] = weight
    return weights


def _backtest_sleeve_point(
    point_date: date,
    weights_by_instrument: dict[str, float],
    top_lookup: dict[str, tuple[str | None, str]],
    *,
    cash_weight: float,
    derivative_weight: float = 0.0,
) -> dict[str, object]:
    sleeve_by_key: dict[str, dict[str, object]] = {}
    for instrument_id, weight in weights_by_instrument.items():
        if abs(weight) <= 1e-12:
            continue
        top_id, top_label = top_lookup.get(instrument_id, (None, "Unassigned"))
        top_key = top_id or "__unassigned__"
        sleeve = sleeve_by_key.setdefault(
            top_key,
            {
                "top_sleeve_id": top_id,
                "top_sleeve_label": top_label,
                "value": 0.0,
            },
        )
        sleeve["value"] = float(sleeve["value"] or 0.0) + weight
    if abs(cash_weight) > 1e-12:
        sleeve_by_key[SYSTEM_CASH_TARGET_MEMBER_ID] = {
            "top_sleeve_id": SYSTEM_CASH_TARGET_MEMBER_ID,
            "top_sleeve_label": SYSTEM_CASH_TARGET_LABEL,
            "value": cash_weight,
        }
    if abs(derivative_weight) > 1e-12:
        sleeve_by_key[SYSTEM_DERIVATIVE_TARGET_MEMBER_ID] = {
            "top_sleeve_id": SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
            "top_sleeve_label": SYSTEM_DERIVATIVE_TARGET_LABEL,
            "value": derivative_weight,
        }
    return {
        "date": point_date.isoformat(),
        "sleeves": sorted(
            sleeve_by_key.values(),
            key=lambda item: abs(_safe_float(item.get("value")) or 0.0),
            reverse=True,
        ),
    }


def _validate_backtest_assumptions(
    *,
    cash_yield_annual: float,
    commission_bps: float,
    tax_bps: float,
    slippage_bps: float,
    implementation_delay_days: int,
) -> None:
    if not -1.0 <= cash_yield_annual <= 1.0:
        raise ValueError("Backtest annual cash yield must be between -100% and 100%.")
    if min(commission_bps, tax_bps, slippage_bps) < 0.0:
        raise ValueError("Backtest commission, tax, and slippage assumptions cannot be negative.")
    if implementation_delay_days < 0:
        raise ValueError("Backtest implementation delay cannot be negative.")


def _is_derivative_capital_activity(transaction: dict[str, object]) -> bool:
    if not (
        str(transaction.get("derivative_contract_id") or "").strip()
        or holdings_market_profile.is_derivative_contract(
            transaction.get("derivative_contract")
        )
    ):
        return False
    transaction_type = str(transaction.get("transaction_type") or "").strip()
    lifecycle_event_type = str(
        transaction.get("lifecycle_event_type") or ""
    ).strip()
    option_action = str(transaction.get("option_action") or "").strip()
    if option_action or lifecycle_event_type.startswith(("fcn_", "option_")):
        return True
    return transaction_type not in {"coupon", "dividend", "interest"}


def _derivative_carrying_value_from_statement(
    statement: dict[str, object],
) -> float:
    total = 0.0
    for position in list(statement.get("positions") or []):
        if not isinstance(position, dict) or not holdings_market_profile.is_derivative_contract(
            position.get("derivative_contract")
        ):
            continue
        value = _safe_float(position.get("market_value_base"))
        if value is None:
            raise ValueError(
                "Historical derivative carrying value is incomplete; refresh price/FX coverage before backtesting."
            )
        total += value
    return float(total)


def _build_derivative_backtest_context(
    portfolio_id: str,
    *,
    start_date: date,
    end_date: date,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object]:
    transactions = list_transactions(portfolio_id)
    derivative_transactions = [
        transaction
        for transaction in transactions
        if _is_derivative_capital_activity(transaction)
        and (transaction_performance_effective_date(transaction) or date.max)
        <= end_date
    ]
    if not derivative_transactions:
        return {
            "base_nav": None,
            "events": [],
            "nav_points": [],
            "warnings": [],
        }

    snapshots = list_materialized_daily_snapshots(
        portfolio_id,
        start_date=None,
        end_date=end_date,
        ensure_current=True,
    )
    nav_points = sorted(
        [
            (snapshot_date, float(nav))
            for snapshot in snapshots
            if (snapshot_date := _parse_iso_date(snapshot.get("as_of_date")))
            is not None
            and (nav := _safe_float(snapshot.get("nav"))) is not None
            and nav > 1e-12
        ],
        key=lambda item: item[0],
    )
    if not nav_points:
        raise ValueError(
            "Derivative capital backtest requires a positive, materialized portfolio NAV history."
        )
    base_candidates = [item for item in nav_points if item[0] <= start_date]
    base_date, base_nav = (
        base_candidates[-1]
        if base_candidates
        else next((item for item in nav_points if item[0] >= start_date), nav_points[0])
    )
    if base_nav <= 1e-12:
        raise ValueError("Derivative capital backtest requires positive starting NAV.")

    available_dates = [item[0] for item in nav_points]

    def first_snapshot_on_or_after(point_date: date) -> date | None:
        return next((item for item in available_dates if item >= point_date), None)

    event_dates = {base_date}
    for transaction in derivative_transactions:
        effective_date = transaction_performance_effective_date(transaction)
        if effective_date is None:
            continue
        snapshot_date = first_snapshot_on_or_after(max(effective_date, base_date))
        if snapshot_date is not None and snapshot_date <= end_date:
            event_dates.add(snapshot_date)

    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")
    accounts = list_accounts(portfolio_id)
    nav_by_date = dict(nav_points)
    events: list[dict[str, object]] = []
    previous_target_value: float | None = None
    for event_date in sorted(event_dates):
        statement = build_holdings_report(
            portfolio,
            accounts,
            transactions,
            as_of_date=event_date,
            instrument_detail_cache=instrument_detail_cache,
        )
        actual_value_base = _derivative_carrying_value_from_statement(statement)
        target_value = actual_value_base / base_nav
        if previous_target_value is None and abs(target_value) <= 1e-12:
            # Zero is the replay default.  Do not present a synthetic backtest
            # anchor as though it were an FCN/option lifecycle event.
            previous_target_value = 0.0
            continue
        if (
            previous_target_value is not None
            and abs(target_value - previous_target_value) <= 1e-12
        ):
            continue
        reference_nav = nav_by_date.get(event_date)
        if reference_nav is None or reference_nav <= 1e-12:
            raise ValueError(
                f"{event_date.isoformat()} derivative capital event lacks a positive portfolio NAV."
            )
        events.append(
            {
                "effective_date": event_date.isoformat(),
                "actual_value_base": actual_value_base,
                "target_value": target_value,
                "reference_weight": actual_value_base / reference_nav,
                "source": "actual_derivative_ledger",
            }
        )
        previous_target_value = target_value

    return {
        "base_nav": base_nav,
        "base_date": base_date.isoformat(),
        "events": events,
        "nav_points": [
            {"date": point_date.isoformat(), "nav": nav}
            for point_date, nav in nav_points
        ],
        "warnings": [
            "Derivative capital is replayed from actual position-changing FCN/option ledger dates in starting-NAV units. "
            "Ordinary security rebalances cannot change it; changes in coupon/payoff value beyond carrying capital remain unmodeled."
        ],
    }


def _derivative_backtest_state_on(
    context: dict[str, object],
    *,
    point_date: date,
) -> tuple[float, float]:
    events = [
        item
        for item in list(context.get("events") or [])
        if isinstance(item, dict)
        and (_parse_iso_date(item.get("effective_date")) or date.max) <= point_date
    ]
    if not events:
        return 0.0, 0.0
    latest_event = events[-1]
    target_value = float(_safe_float(latest_event.get("target_value")) or 0.0)
    actual_value_base = float(
        _safe_float(latest_event.get("actual_value_base")) or 0.0
    )
    nav_points = [
        (parsed_date, float(nav))
        for item in list(context.get("nav_points") or [])
        if isinstance(item, dict)
        and (parsed_date := _parse_iso_date(item.get("date"))) is not None
        and parsed_date <= point_date
        and (nav := _safe_float(item.get("nav"))) is not None
        and nav > 1e-12
    ]
    if not nav_points:
        raise ValueError(
            f"{point_date.isoformat()} derivative capital reference lacks positive actual NAV."
        )
    return target_value, actual_value_base / nav_points[-1][1]


def _first_common_return_date_on_or_after(
    instrument_ids: list[str],
    returns_by_instrument: dict[str, pd.Series],
    earliest_date: date,
    end_date: date,
) -> date | None:
    if not instrument_ids:
        return earliest_date if earliest_date <= end_date else None
    eligible_sets: list[set[date]] = []
    for instrument_id in instrument_ids:
        series = returns_by_instrument.get(instrument_id)
        if series is None or series.empty:
            return None
        eligible_sets.append(
            {
                item
                for item in series.index
                if isinstance(item, date) and earliest_date <= item <= end_date
            }
        )
    common_dates = set.intersection(*eligible_sets) if eligible_sets else set()
    return min(common_dates) if common_dates else None


def _replay_backtest_decisions(
    decisions: list[dict[str, object]],
    *,
    returns_by_instrument: dict[str, pd.Series],
    as_of_date: date,
    cash_yield_annual: float,
    commission_bps: float,
    tax_bps: float,
    slippage_bps: float,
    implementation_delay_days: int,
    derivative_capital_events: list[dict[str, object]] | None = None,
    cash_borrowing_allowed: bool = False,
) -> dict[str, object]:
    _validate_backtest_assumptions(
        cash_yield_annual=cash_yield_annual,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
        slippage_bps=slippage_bps,
        implementation_delay_days=implementation_delay_days,
    )
    if not decisions:
        return {
            "points": [],
            "returns": {},
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "contribution_reconciliation_points": [],
            "execution_records": [],
            "derivative_capital_events": [],
            "skipped_executions": [],
            "total_turnover": 0.0,
            "total_cost": 0.0,
            "warnings": [],
        }

    resolved_decisions: list[dict[str, object]] = []
    replay_warnings: list[str] = []
    skipped_executions: list[dict[str, str]] = []
    pending_executions: list[dict[str, str]] = []
    normalized_derivative_events: list[dict[str, object]] = []
    for raw_event in derivative_capital_events or []:
        if not isinstance(raw_event, dict):
            continue
        effective_date = _parse_iso_date(raw_event.get("effective_date"))
        target_value = _safe_float(raw_event.get("target_value"))
        if effective_date is None or effective_date > as_of_date or target_value is None:
            continue
        if not np.isfinite(target_value):
            raise ValueError("Derivative capital events require finite target values.")
        normalized_derivative_events.append(
            {
                **deepcopy(raw_event),
                "effective_date": effective_date.isoformat(),
                "target_value": float(target_value),
            }
        )
    normalized_derivative_events.sort(key=lambda item: str(item["effective_date"]))
    for decision in decisions:
        decision_date = _parse_iso_date(decision.get("decision_date"))
        if decision_date is None:
            raise ValueError("Backtest decision is missing a valid decision date.")
        decision_delay_days = int(
            decision.get("implementation_delay_days")
            if decision.get("implementation_delay_days") is not None
            else implementation_delay_days
        )
        if decision_delay_days < 0:
            raise ValueError("Backtest decision implementation delay cannot be negative.")
        scheduled_date = decision_date + timedelta(days=decision_delay_days)
        if scheduled_date > as_of_date:
            pending_executions.append({
                "date": decision_date.isoformat(),
                "reason": f"Scheduled execution {scheduled_date.isoformat()} is after the backtest cutoff {as_of_date.isoformat()}.",
            })
            continue
        target_weights = {
            str(item.get("instrument_id") or ""): float(
                _safe_float(item.get("target_weight")) or 0.0
            )
            for item in list(decision.get("target_weights") or [])
            if isinstance(item, dict) and str(item.get("instrument_id") or "").strip()
        }
        actual_date = _first_common_return_date_on_or_after(
            list(target_weights),
            returns_by_instrument,
            scheduled_date,
            as_of_date,
        )
        if actual_date is None:
            reason = (
                f"{decision_date.isoformat()} decision could not execute by {as_of_date.isoformat()} "
                "because its target instruments lack a common post-delay observation."
            )
            skipped_executions.append(
                {"date": decision_date.isoformat(), "reason": reason}
            )
            replay_warnings.append(reason)
            continue
        resolved_decisions.append(
            {
                **deepcopy(decision),
                "implementation_delay_days": decision_delay_days,
                "scheduled_execution_date": scheduled_date.isoformat(),
                "actual_execution_date": actual_date.isoformat(),
                "target_weight_map": target_weights,
            }
        )

    retained_decisions: list[dict[str, object]] = []
    for decision in resolved_decisions:
        superseding = next(
            (
                later for later in decisions
                if str(later.get("decision_date") or "") > str(decision["decision_date"])
                and date.fromisoformat(str(later["decision_date"]))
                + timedelta(
                    days=int(
                        later.get("implementation_delay_days")
                        if later.get("implementation_delay_days") is not None
                        else implementation_delay_days
                    )
                )
                <= date.fromisoformat(str(decision["actual_execution_date"]))
            ),
            None,
        )
        if superseding is not None:
            reason = (
                f"{decision['decision_date']} pending target was superseded by the "
                f"{superseding['decision_date']} decision before it could execute."
            )
            skipped_executions.append({"date": str(decision["decision_date"]), "reason": reason})
            replay_warnings.append(reason)
        else:
            retained_decisions.append(decision)
    resolved_decisions = retained_decisions

    if not resolved_decisions:
        return {
            "points": [],
            "returns": {},
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "contribution_reconciliation_points": [],
            "execution_records": [],
            "derivative_capital_events": [],
            "skipped_executions": skipped_executions,
            "pending_executions": pending_executions,
            "total_turnover": 0.0,
            "total_cost": 0.0,
            "warnings": replay_warnings,
        }

    resolved_decisions.sort(
        key=lambda item: (
            str(item.get("actual_execution_date") or ""),
            str(item.get("decision_date") or ""),
        )
    )
    executions_by_date: dict[date, list[dict[str, object]]] = defaultdict(list)
    for decision in resolved_decisions:
        execution_date = _parse_iso_date(decision.get("actual_execution_date"))
        if execution_date is not None:
            executions_by_date[execution_date].append(decision)
    derivative_events_by_date: dict[date, list[dict[str, object]]] = defaultdict(list)
    for event in normalized_derivative_events:
        effective_date = _parse_iso_date(event.get("effective_date"))
        if effective_date is not None:
            derivative_events_by_date[effective_date].append(event)

    all_return_dates = {
        item
        for series in returns_by_instrument.values()
        for item in series.index
        if isinstance(item, date) and item <= as_of_date
    }
    event_dates = sorted(
        all_return_dates
        .union(executions_by_date)
        .union(derivative_events_by_date)
        .union({as_of_date})
    )
    first_execution_date = min(executions_by_date)
    first_decision_date = min(
        _parse_iso_date(item.get("decision_date")) or first_execution_date
        for item in resolved_decisions
    )
    artificial_anchor = first_execution_date <= first_decision_date
    anchor_date = (
        first_execution_date - timedelta(days=1)
        if artificial_anchor
        else first_decision_date
    )

    risky_values: dict[str, float] = {}
    top_lookup: dict[str, tuple[str | None, str]] = {}
    initial_derivative_events = [
        event
        for event in normalized_derivative_events
        if (_parse_iso_date(event.get("effective_date")) or date.max) <= anchor_date
    ]
    derivative_value = (
        float(initial_derivative_events[-1]["target_value"])
        if initial_derivative_events
        else 0.0
    )
    cash_value = 1.0 - derivative_value
    nav_value = 1.0
    previous_event_date = anchor_date
    points: list[dict[str, object]] = [{"date": anchor_date.isoformat(), "value": nav_value}]
    portfolio_returns: dict[str, float] = {}
    weight_points: list[dict[str, object]] = [
        _backtest_sleeve_point(
            anchor_date,
            {},
            {},
            cash_weight=cash_value,
            derivative_weight=derivative_value,
        )
    ]
    cumulative_contribution: dict[str, dict[str, object]] = {
        SYSTEM_CASH_TARGET_MEMBER_ID: {
            "top_sleeve_id": SYSTEM_CASH_TARGET_MEMBER_ID,
            "top_sleeve_label": SYSTEM_CASH_TARGET_LABEL,
            "value": 0.0,
        },
        "__execution_costs__": {
            "top_sleeve_id": "__execution_costs__",
            "top_sleeve_label": "Execution Costs",
            "value": 0.0,
        },
    }
    contribution_points: list[dict[str, object]] = []
    reconciliation_points: list[dict[str, object]] = []
    execution_records: list[dict[str, object]] = []
    applied_derivative_events: list[dict[str, object]] = [
        {
            **deepcopy(event),
            "previous_value": 0.0 if index == 0 else float(initial_derivative_events[index - 1]["target_value"]),
            "capital_change": float(event["target_value"])
            - (0.0 if index == 0 else float(initial_derivative_events[index - 1]["target_value"])),
        }
        for index, event in enumerate(initial_derivative_events)
    ]
    total_turnover = 0.0
    total_cost = 0.0
    first_processed_event = True
    previous_return_boundary_date = anchor_date

    for event_date in event_dates:
        if event_date <= anchor_date:
            continue
        due_executions = executions_by_date.get(event_date, [])
        due_derivative_events = derivative_events_by_date.get(event_date, [])
        if due_executions:
            valid_executions: list[dict[str, object]] = []
            for decision in due_executions:
                decision_date = _parse_iso_date(decision.get("decision_date"))
                intervening_derivative_events = [
                    item
                    for item in normalized_derivative_events
                    if decision_date is not None
                    and decision_date
                    < (_parse_iso_date(item.get("effective_date")) or date.min)
                    <= event_date
                ]
                if not intervening_derivative_events:
                    valid_executions.append(decision)
                    continue
                changed_dates = ", ".join(
                    dict.fromkeys(
                        str(item.get("effective_date"))
                        for item in intervening_derivative_events
                    )
                )
                decision_date_label = str(
                    decision.get("decision_date") or event_date.isoformat()
                )
                reason = (
                    f"{decision_date_label} target invalidated before execution because "
                    f"frozen derivative capital changed on {changed_dates}; the security "
                    "rebalance was not executed from a stale capital budget."
                )
                skipped_executions.append(
                    {"date": decision_date_label, "reason": reason}
                )
                replay_warnings.append(reason)
            due_executions = valid_executions
        active_before = [
            instrument_id
            for instrument_id, value in risky_values.items()
            if abs(value) > 1e-12
        ]
        complete_return_date = bool(active_before) and all(
            event_date in returns_by_instrument[instrument_id].index
            for instrument_id in active_before
        )
        if not due_executions and not due_derivative_events and not complete_return_date:
            if not active_before and event_date == as_of_date:
                complete_return_date = True
            else:
                continue

        if due_executions and active_before and not complete_return_date:
            missing_instruments = sorted(
                instrument_id
                for instrument_id in active_before
                if event_date not in returns_by_instrument[instrument_id].index
            )
            missing_label = ", ".join(missing_instruments)
            for decision in due_executions:
                decision_date = str(decision.get("decision_date") or event_date.isoformat())
                reason = (
                    f"{event_date.isoformat()} execution skipped because pre-trade holdings "
                    f"lack a complete EOD return observation: {missing_label}."
                )
                skipped_executions.append({"date": decision_date, "reason": reason})
                replay_warnings.append(reason)
            # A stale pre-trade NAV is not an executable valuation. Keep the
            # existing holdings; a separately recorded derivative lifecycle
            # event may still update its own carrying-capital ledger.
            due_executions = []
            if not due_derivative_events:
                continue

        prior_nav = nav_value
        elapsed_days = max((event_date - previous_event_date).days, 0)
        if artificial_anchor and first_processed_event:
            elapsed_days = 0
        cash_return = (
            (1.0 + cash_yield_annual) ** (elapsed_days / 365.25) - 1.0
            if elapsed_days > 0
            else 0.0
        )
        cash_profit = cash_value * cash_return
        cash_value += cash_profit
        cumulative_contribution[SYSTEM_CASH_TARGET_MEMBER_ID]["value"] = (
            float(cumulative_contribution[SYSTEM_CASH_TARGET_MEMBER_ID]["value"] or 0.0)
            + cash_profit
        )

        # Registry observations are EOD period-end values.  Existing holdings
        # earn the return ending on this date before an EOD rebalance is
        # applied; a position bought at this date cannot consume the
        # close-to-close return that ended at the execution observation.
        if complete_return_date:
            for instrument_id in active_before:
                series = returns_by_instrument[instrument_id]
                interval_returns = series.loc[
                    (series.index > previous_return_boundary_date) & (series.index <= event_date)
                ]
                if interval_returns.empty or not np.isfinite(interval_returns.to_numpy(dtype="float64")).all():
                    raise ValueError(
                        f"{event_date.isoformat()} has an invalid return for {instrument_id}."
                    )
                # A shared valuation date may span several observations of a
                # faster-publishing member. None of those returns may be lost.
                instrument_return = float((1.0 + interval_returns).prod() - 1.0)
                instrument_profit = risky_values[instrument_id] * instrument_return
                risky_values[instrument_id] += instrument_profit
                top_id, top_label = top_lookup.get(
                    instrument_id, (None, "Unassigned")
                )
                top_key = top_id or "__unassigned__"
                sleeve = cumulative_contribution.setdefault(
                    top_key,
                    {
                        "top_sleeve_id": top_id,
                        "top_sleeve_label": top_label,
                        "value": 0.0,
                    },
                )
                sleeve["value"] = float(sleeve["value"] or 0.0) + instrument_profit
            previous_return_boundary_date = event_date

        for derivative_event in due_derivative_events:
            target_derivative_value = float(derivative_event["target_value"])
            previous_derivative_value = derivative_value
            derivative_change = target_derivative_value - previous_derivative_value
            cash_value -= derivative_change
            derivative_value = target_derivative_value
            applied_derivative_events.append(
                {
                    **deepcopy(derivative_event),
                    "previous_value": previous_derivative_value,
                    "capital_change": derivative_change,
                }
            )

        for decision in due_executions:
            nav_before_trade = cash_value + derivative_value + sum(risky_values.values())
            if nav_before_trade <= 0.0:
                raise ValueError(
                    f"{event_date.isoformat()} backtest NAV became non-positive before execution."
                )
            target_weight_map = dict(decision.get("target_weight_map") or {})
            derivative_reference_weight = float(
                decision.get("derivative_reference_weight")
                if decision.get("derivative_reference_weight") is not None
                else decision.get("derivative_target_weight")
                or 0.0
            )
            all_instruments = set(risky_values).union(target_weight_map)

            def cost_at_nav(target_nav: float) -> float:
                amounts = [
                    float(target_weight_map.get(instrument_id, 0.0)) * target_nav
                    - risky_values.get(instrument_id, 0.0)
                    for instrument_id in all_instruments
                ]
                return (
                    sum(abs(amount) for amount in amounts) * (commission_bps + slippage_bps)
                    + sum(max(-amount, 0.0) for amount in amounts) * tax_bps
                ) / 10_000.0

            # Targets are weights of post-cost NAV. Funding costs out of an
            # already fully invested pre-cost target would create hidden debt.
            if cost_at_nav(0.0) >= nav_before_trade:
                raise ValueError(f"{event_date.isoformat()} execution costs exhaust backtest NAV.")
            investable_nav = brentq(
                lambda value: value + cost_at_nav(value) - nav_before_trade,
                0.0,
                nav_before_trade,
                xtol=1e-14,
            )
            target_values = {
                instrument_id: float(weight) * investable_nav
                for instrument_id, weight in target_weight_map.items()
            }
            trades = {
                instrument_id: target_values.get(instrument_id, 0.0)
                - risky_values.get(instrument_id, 0.0)
                for instrument_id in all_instruments
            }
            buy_amount = sum(max(amount, 0.0) for amount in trades.values())
            sell_amount = sum(max(-amount, 0.0) for amount in trades.values())
            derivative_target_value = derivative_value
            derivative_target_weight = (
                derivative_target_value / investable_nav
                if abs(investable_nav) > 1e-12
                else 0.0
            )
            target_cash_value = (
                investable_nav - sum(target_values.values()) - derivative_target_value
            )
            target_cash_weight = target_cash_value / investable_nav
            derivative_leg = 0.0
            cash_leg = abs(target_cash_value - cash_value) / nav_before_trade
            buy_turnover = buy_amount / nav_before_trade
            sell_turnover = sell_amount / nav_before_trade
            one_way_turnover = 0.5 * (buy_turnover + sell_turnover + cash_leg + derivative_leg)
            commission_cost = (buy_amount + sell_amount) * commission_bps / 10_000.0
            tax_cost = sell_amount * tax_bps / 10_000.0
            slippage_cost = (buy_amount + sell_amount) * slippage_bps / 10_000.0
            execution_cost = commission_cost + tax_cost + slippage_cost

            risky_values = {
                instrument_id: value
                for instrument_id, value in target_values.items()
                if abs(value) > 1e-12
            }
            cash_value = target_cash_value
            nav_after_trade = cash_value + derivative_value + sum(risky_values.values())
            if nav_after_trade <= 0.0:
                raise ValueError(
                    f"{event_date.isoformat()} backtest NAV became non-positive after execution costs."
                )
            decision_top_lookup = {
                str(item.get("instrument_id") or ""): (
                    str(item.get("top_sleeve_id") or "") or None,
                    str(item.get("top_sleeve_label") or "Unassigned"),
                )
                for item in list(decision.get("target_weights") or [])
                if isinstance(item, dict) and str(item.get("instrument_id") or "").strip()
            }
            top_lookup = decision_top_lookup
            cumulative_contribution["__execution_costs__"]["value"] = (
                float(cumulative_contribution["__execution_costs__"]["value"] or 0.0)
                - execution_cost
            )
            total_turnover += one_way_turnover
            total_cost += execution_cost
            execution_records.append(
                {
                    "decision_date": decision.get("decision_date"),
                    "scheduled_execution_date": decision.get(
                        "scheduled_execution_date"
                    ),
                    "actual_execution_date": event_date.isoformat(),
                    "taxonomy_configuration_version": decision.get(
                        "taxonomy_configuration_version"
                    ),
                    "taxonomy_configuration_effective_from": decision.get(
                        "taxonomy_configuration_effective_from"
                    ),
                    "target_weights": deepcopy(decision.get("target_weights") or []),
                    "cash_target_weight": target_cash_weight,
                    "derivative_target_weight": derivative_target_weight,
                    "derivative_reference_weight": derivative_reference_weight,
                    "derivative_target_value": derivative_target_value,
                    "derivative_no_trade": True,
                    "risky_buy_turnover": buy_turnover,
                    "risky_sell_turnover": sell_turnover,
                    "cash_leg_turnover": cash_leg,
                    "derivative_leg_turnover": derivative_leg,
                    "one_way_turnover": one_way_turnover,
                    "commission_cost": commission_cost,
                    "tax_cost": tax_cost,
                    "slippage_cost": slippage_cost,
                    "total_cost": execution_cost,
                    "nav_before_execution": nav_before_trade,
                    "nav_after_execution": nav_after_trade,
                }
            )
        if cash_value < -1e-10 and not cash_borrowing_allowed:
            event_label = ", ".join(
                str(item.get("effective_date") or event_date.isoformat())
                for item in due_derivative_events
            )
            raise ValueError(
                f"{event_date.isoformat()} backtest requires implicit cash borrowing after "
                + (
                    f"the frozen derivative capital change on {event_label}"
                    if due_derivative_events
                    else "the target execution"
                )
                + "; record or model an executable funding rebalance instead."
            )
        if due_executions:
            # Newly executed holdings start after this EOD boundary and must
            # not consume returns that ended on the execution date.
            previous_return_boundary_date = event_date

        nav_value = cash_value + derivative_value + sum(risky_values.values())
        if nav_value <= 0.0:
            raise ValueError(
                f"{event_date.isoformat()} backtest portfolio NAV became non-positive."
            )
        date_key = event_date.isoformat()
        if abs(prior_nav) > 1e-12:
            portfolio_returns[date_key] = nav_value / prior_nav - 1.0
        points.append({"date": date_key, "value": nav_value})
        weights_by_instrument = {
            instrument_id: value / nav_value
            for instrument_id, value in risky_values.items()
        }
        weight_points.append(
            _backtest_sleeve_point(
                event_date,
                weights_by_instrument,
                top_lookup,
                cash_weight=cash_value / nav_value,
                derivative_weight=derivative_value / nav_value,
            )
        )
        sleeves = [
            deepcopy(item)
            for item in cumulative_contribution.values()
            if abs(float(_safe_float(item.get("value")) or 0.0)) > 1e-15
        ]
        sleeves.sort(
            key=lambda item: abs(_safe_float(item.get("value")) or 0.0),
            reverse=True,
        )
        contribution_points.append({"date": date_key, "sleeves": sleeves})
        cumulative_total = sum(
            float(_safe_float(item.get("value")) or 0.0)
            for item in cumulative_contribution.values()
        )
        reconciliation_points.append(
            {
                "date": date_key,
                "nav_change": nav_value - 1.0,
                "linked_contribution": cumulative_total,
                "residual": nav_value - 1.0 - cumulative_total,
                "execution_cost_contribution": float(
                    cumulative_contribution["__execution_costs__"]["value"] or 0.0
                ),
            }
        )
        previous_event_date = event_date
        first_processed_event = False

    return {
        "points": points,
        "returns": portfolio_returns,
        "top_sleeve_weight_points": weight_points,
        "top_sleeve_contribution_points": contribution_points,
        "contribution_reconciliation_points": reconciliation_points,
        "execution_records": execution_records,
        "derivative_capital_events": applied_derivative_events,
        "skipped_executions": skipped_executions,
        "pending_executions": pending_executions,
        "total_turnover": total_turnover,
        "total_cost": total_cost,
        "warnings": replay_warnings,
    }


def _normalized_window_points(
    points: list[dict[str, object]],
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    normalized = _normalized_backtest_points(points)
    anchor_candidates = [
        item
        for item in normalized
        if (_parse_iso_date(item.get("date")) or date.max) < start_date
    ]
    if not anchor_candidates:
        return []
    anchor = anchor_candidates[-1]
    anchor_value = _safe_float(anchor.get("value"))
    if anchor_value is None or anchor_value <= 0.0:
        return []
    selected = [anchor]
    selected.extend(
        item
        for item in normalized
        if start_date <= (_parse_iso_date(item.get("date")) or date.min) <= end_date
    )
    return [
        {"date": item["date"], "value": float(item["value"]) / anchor_value}
        for item in selected
    ]


def _rolling_holdout_metadata() -> dict[str, object]:
    return {
        "validation_method": "rolling_temporal_holdout",
        "parameter_selection": "fixed_point_in_time_policy",
        "parameter_optimization": False,
        "methodology_note": (
            "Training and test dates are temporal diagnostics over the already replayed "
            "fixed-policy series. No parameters are fitted on the training window and "
            "frozen for a separate test rerun; this is not walk-forward optimization."
        ),
    }


def _build_walk_forward_validation(
    points: list[dict[str, object]],
    decisions: list[dict[str, object]],
    *,
    training_months: int,
    test_months: int,
) -> dict[str, object]:
    """Build rolling temporal holdout diagnostics for a fixed policy.

    The public key remains ``walk_forward`` for API compatibility, but the
    payload explicitly identifies that this function does not optimize or
    refit parameters inside each training window.
    """
    if training_months <= 0 or test_months <= 0:
        raise ValueError("Walk-forward training and test windows must be positive.")
    normalized = _normalized_backtest_points(points)
    if len(normalized) < 2:
        return {
            **_rolling_holdout_metadata(),
            "available": False,
            "unavailable_reason": "Rolling temporal holdout requires a non-empty backtest history.",
            "training_months": training_months,
            "test_months": test_months,
            "windows": [],
            "oos_points": [],
            "oos_metrics": _build_backtest_metrics([], {}),
        }
    first_date = _parse_iso_date(normalized[0].get("date"))
    last_date = _parse_iso_date(normalized[-1].get("date"))
    if first_date is None or last_date is None:
        raise ValueError("Backtest points contain invalid dates.")
    test_start = (
        pd.Timestamp(first_date) + pd.DateOffset(months=training_months)
    ).date()
    if test_start >= last_date:
        return {
            **_rolling_holdout_metadata(),
            "available": False,
            "unavailable_reason": (
                f"History is shorter than the configured {training_months}-month training window "
                "plus an out-of-sample observation."
            ),
            "training_months": training_months,
            "test_months": test_months,
            "windows": [],
            "oos_points": [],
            "oos_metrics": _build_backtest_metrics([], {}),
        }

    windows: list[dict[str, object]] = []
    aggregate_oos_returns: dict[str, float] = {}
    while test_start < last_date:
        test_end = min(
            (
                pd.Timestamp(test_start) + pd.DateOffset(months=test_months)
            ).date()
            - timedelta(days=1),
            last_date,
        )
        training_start = (
            pd.Timestamp(test_start) - pd.DateOffset(months=training_months)
        ).date()
        training_end = test_start - timedelta(days=1)
        window_points = _normalized_window_points(
            normalized,
            start_date=test_start,
            end_date=test_end,
        )
        window_returns = _backtest_return_map_from_points(window_points)
        aggregate_oos_returns.update(window_returns)
        versions = sorted(
            {
                int(item["taxonomy_configuration_version"])
                for item in decisions
                if item.get("taxonomy_configuration_version") is not None
                and test_start
                <= (_parse_iso_date(item.get("decision_date")) or date.min)
                <= test_end
            }
        )
        windows.append(
            {
                "training_start_date": training_start.isoformat(),
                "training_end_date": training_end.isoformat(),
                "test_start_date": test_start.isoformat(),
                "test_end_date": test_end.isoformat(),
                "configuration_versions_used": versions,
                "points": window_points,
                "metrics": _build_backtest_metrics(window_points, window_returns),
                "available": len(window_points) >= 2,
                "unavailable_reason": (
                    None
                    if len(window_points) >= 2
                    else "No complete out-of-sample return observation exists in this test window."
                ),
            }
        )
        test_start = (
            pd.Timestamp(test_start) + pd.DateOffset(months=test_months)
        ).date()

    oos_points: list[dict[str, object]] = []
    oos_nav = 1.0
    if aggregate_oos_returns:
        first_oos_date = min(date.fromisoformat(item) for item in aggregate_oos_returns)
        oos_anchor = max(
            date.fromisoformat(str(item["date"]))
            for item in normalized
            if date.fromisoformat(str(item["date"])) < first_oos_date
        )
        oos_points.append(
            {
                "date": oos_anchor.isoformat(),
                "value": oos_nav,
            }
        )
        for date_key in sorted(aggregate_oos_returns):
            oos_nav *= 1.0 + aggregate_oos_returns[date_key]
            oos_points.append({"date": date_key, "value": oos_nav})
    available_windows = [item for item in windows if bool(item.get("available"))]
    return {
        **_rolling_holdout_metadata(),
        "available": bool(available_windows),
        "unavailable_reason": (
            None
            if available_windows
            else "No configured rolling holdout test window contains a complete out-of-sample return."
        ),
        "training_months": training_months,
        "test_months": test_months,
        "windows": windows,
        "oos_points": oos_points,
        "oos_metrics": _build_backtest_metrics(oos_points, aggregate_oos_returns),
    }


def _empty_point_in_time_backtest(
    *,
    rebalance_frequency: str,
    as_of_date: date,
    lookback_days: int,
    warnings: list[str],
    unavailable_reason: str,
    methodology: dict[str, object],
) -> dict[str, object]:
    return {
        "rebalance_frequency": rebalance_frequency,
        "common_history_start_date": None,
        "start_date": None,
        "end_date": as_of_date.isoformat(),
        "lookback_days": lookback_days,
        "points": [],
        "metrics": _build_backtest_metrics([], {}),
        "top_sleeve_weight_points": [],
        "top_sleeve_contribution_points": [],
        "contribution_reconciliation_points": [],
        "execution_records": [],
        "derivative_capital_events": [],
        "total_turnover": 0.0,
        "total_cost": 0.0,
        "methodology": methodology,
        "point_in_time_coverage": {
            "status": "unavailable",
            "decision_count": 0,
            "first_decision_date": None,
            "last_decision_date": None,
            "configuration_versions_used": [],
            "historical_instrument_count": 0,
            "first_usable_observation_by_instrument": {},
            "skipped_rebalances": [],
            "unavailable_reason": unavailable_reason,
        },
        "robustness_results": [],
        "walk_forward": {
            **_rolling_holdout_metadata(),
            "available": False,
            "unavailable_reason": unavailable_reason,
            "training_months": 0,
            "test_months": 0,
            "windows": [],
            "oos_points": [],
            "oos_metrics": _build_backtest_metrics([], {}),
        },
        "warnings": list(dict.fromkeys([*warnings, unavailable_reason])),
    }


def build_current_target_backtest(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: str = "daily",
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    frozen_taxonomy_node_ids: list[str] | None = None,
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    risk_model_config: dict[str, object] | None = None,
    rebalance_frequency: str = "1m",
    benchmark_instrument_id: str | None = None,
    cash_yield_annual: float = 0.02,
    commission_bps: float = 2.0,
    tax_bps: float = 10.0,
    slippage_bps: float = 5.0,
    implementation_delay_days: int = 1,
    robustness_scenarios: list[dict[str, object]] | None = None,
    walk_forward_training_months: int = 24,
    walk_forward_test_months: int = 6,
    _instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    _direct_fx_instruments: dict[tuple[str, str], str] | None = None,
) -> dict[str, object]:
    _validate_research_solve_configuration(
        calculation_frequency=calculation_frequency,
        target_dimension=target_dimension,
        capital_mode=capital_mode,
        gross_exposure=gross_exposure,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
    )
    frequency = _normalize_backtest_rebalance_frequency(rebalance_frequency)
    _validate_backtest_assumptions(
        cash_yield_annual=cash_yield_annual,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
        slippage_bps=slippage_bps,
        implementation_delay_days=implementation_delay_days,
    )
    warnings: list[str] = list(RESEARCH_BACKTEST_METHODOLOGY_WARNINGS)
    methodology = {
        "name": "Point-in-time target-policy simulation",
        "point_in_time_universe": True,
        "point_in_time_taxonomy": True,
        "decision_rule": (
            "Each scheduled rebalance, plus each recorded derivative-capital lifecycle date, "
            "solves weights from only the taxonomy configuration, targets, and market observations "
            "available on that decision date."
        ),
        "execution_rule": (
            "Targets execute at the EOD boundary after the configured calendar-day delay "
            "on the first common return observation for all target instruments; the return "
            "ending on that observation belongs to the pre-execution holdings, so new targets "
            "start accruing from the next observation. If a pre-trade holding lacks a complete "
            "observation at the boundary, the execution is skipped and disclosed as partial "
            "point-in-time coverage rather than using a stale NAV. A derivative-capital "
            "lifecycle change after the decision also invalidates that security rebalance "
            "rather than spending an obsolete capital budget. The replacement funding solve "
            "on the lifecycle date executes at that EOD boundary so fixed capital does not create "
            "implicit cash borrowing."
        ),
        "cash_return_rule": (
            "Only cash compounds from the configured annual yield using actual calendar days / 365.25. "
            "Derivative capital remains a separate zero-return proxy, without modeled coupon, option payoff or FX risk."
        ),
        "derivative_rule": (
            "FCN/options are no-trade. Their signed carrying capital is normalized by actual starting NAV and changes "
            "only on recorded derivative lifecycle dates; ordinary policy rebalances cannot target or resize them. "
            "A lifecycle change triggers a same-day solve of the adjustable book; unless the selected capital mode "
            "explicitly permits gross exposure above 100%, the replay fails if funding cannot preserve non-negative cash."
        ),
        "cost_rule": (
            "Commission and slippage apply to risky buys and sells; tax applies to risky sells. "
            "Target amounts use post-cost NAV so an unlevered allocation does not borrow to fund its fees."
        ),
        "contribution_linking": (
            "Daily component profit is accumulated in starting-NAV units; cash and execution costs "
            "are explicit components and the residual reconciles to ending NAV minus one."
        ),
        "assumptions": {
            "cash_yield_annual": cash_yield_annual,
            "commission_bps": commission_bps,
            "tax_bps": tax_bps,
            "slippage_bps": slippage_bps,
            "implementation_delay_days": implementation_delay_days,
            "derivative_event_funding_delay_days": 0,
            "cash_borrowing_allowed": (
                capital_mode == CAPITAL_MODE_FIXED_GROSS
                and float(gross_exposure or 0.0) > 1.0 + 1e-12
            )
            or (
                capital_mode == CAPITAL_MODE_TARGET_VOLATILITY
                and float(max_gross_exposure or 1.0) > 1.0 + 1e-12
            ),
        },
    }

    revisions = taxonomy_configuration_revisions_through(
        portfolio_id,
        planning_taxonomy_id,
        as_of_date,
    )
    if not revisions:
        empty_backtest = _empty_point_in_time_backtest(
            rebalance_frequency=frequency,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            warnings=warnings,
            unavailable_reason=(
                "Backtest requires at least one effective taxonomy configuration revision on or before the analysis date."
            ),
            methodology=methodology,
        )
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
        }

    shared_detail_cache = (
        _instrument_detail_cache if _instrument_detail_cache is not None else {}
    )
    historical_instrument_ids = _historical_backtest_instrument_ids(
        revisions,
        instrument_detail_cache=shared_detail_cache,
    )
    if not historical_instrument_ids:
        empty_backtest = _empty_point_in_time_backtest(
            rebalance_frequency=frequency,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            warnings=warnings,
            unavailable_reason=(
                "Backtest requires at least one instrument assignment in the effective taxonomy revision history."
            ),
            methodology=methodology,
        )
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
        }

    shared_fx_instruments = (
        _direct_fx_instruments
        if _direct_fx_instruments is not None
        else valuation_fx.fx_direct_instrument_map(get_shared_fx_rates())
    )
    final_state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        frozen_taxonomy_node_ids=frozen_taxonomy_node_ids,
        top_sleeve_weight_bounds=top_sleeve_weight_bounds,
        instrument_detail_cache=shared_detail_cache,
        direct_fx_instruments=shared_fx_instruments,
    )
    nav_by_instrument: dict[str, pd.Series] = {}
    first_observation_by_instrument: dict[str, str] = {}
    for instrument_id in historical_instrument_ids:
        try:
            nav_series, instrument_warnings = _build_instrument_nav_series(
                final_state,
                instrument_id=instrument_id,
                start_date=date(1900, 1, 1),
                end_date=as_of_date,
                warn_on_start_clip=False,
            )
        except ValueError as error:
            warnings.append(f"{instrument_id} excluded from backtest: {error}")
            continue
        if nav_series.empty:
            continue
        nav_by_instrument[instrument_id] = nav_series
        first_observation_by_instrument[instrument_id] = nav_series.index[0].isoformat()
        warnings.extend(instrument_warnings)

    sampled_nav_by_instrument = _build_backtest_sampled_nav_by_instrument(
        nav_by_instrument,
        end_date=as_of_date,
    )
    portfolio_first_dates = [
        series.index[0]
        for series in sampled_nav_by_instrument.values()
        if not series.empty
    ]
    if not sampled_nav_by_instrument or not portfolio_first_dates:
        empty_backtest = _empty_point_in_time_backtest(
            rebalance_frequency=frequency,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            warnings=warnings,
            unavailable_reason="Backtest has no usable point-in-time instrument history.",
            methodology=methodology,
        )
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
        }

    earliest_revision_date = min(
        _parse_iso_date(item.get("effective_from")) or as_of_date
        for item in revisions
    )
    # Assignment/policy inception does not erase already available market
    # history. Each decision independently validates its trailing risk window.
    earliest_start_date = max(earliest_revision_date, min(portfolio_first_dates))
    if earliest_start_date > as_of_date:
        empty_backtest = _empty_point_in_time_backtest(
            rebalance_frequency=frequency,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            warnings=warnings,
            unavailable_reason=(
                "Backtest has no effective configuration and market history by the analysis date."
            ),
            methodology=methodology,
        )
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
        }
    if frozen_taxonomy_node_ids:
        warnings.append(
            "Manual no-trade sleeves are current constraints only and were not backcast into history; "
            "an effective-dated restriction history is required before they can be replayed."
        )
    derivative_context = (
        _build_derivative_backtest_context(
            portfolio_id,
            start_date=earliest_start_date,
            end_date=as_of_date,
            instrument_detail_cache=shared_detail_cache,
        )
        if comparator_taxonomy_node_id is None
        else {"events": [], "nav_points": [], "warnings": []}
    )
    warnings.extend(list(derivative_context.get("warnings") or []))
    returns_by_instrument = {
        instrument_id: _nav_returns(nav)
        for instrument_id, nav in sampled_nav_by_instrument.items()
    }
    scheduled_rebal_dates = _backtest_rebalance_dates(
        start_date=earliest_start_date,
        end_date=as_of_date,
        frequency=frequency,
    )
    derivative_trigger_dates = {
        event_date
        for item in list(derivative_context.get("events") or [])
        if isinstance(item, dict)
        and (event_date := _parse_iso_date(item.get("effective_date"))) is not None
        and earliest_start_date <= event_date <= as_of_date
    }
    rebal_dates = sorted(set(scheduled_rebal_dates).union(derivative_trigger_dates))
    if not rebal_dates:
        rebal_dates = [earliest_start_date]
    if derivative_trigger_dates:
        warnings.append(
            "Recorded FCN/option capital changes trigger same-day funding solves of the adjustable book; "
            "this prevents the frozen capital leg from implying an unconfigured cash borrowing position."
        )

    decisions: list[dict[str, object]] = []
    skipped_rebalance_dates: list[dict[str, str]] = []
    for rebalance_date in rebal_dates:
        try:
            derivative_target_value, derivative_reference_weight = (
                _derivative_backtest_state_on(
                    derivative_context,
                    point_date=rebalance_date,
                )
                if comparator_taxonomy_node_id is None
                else (0.0, 0.0)
            )
            decision_state = _build_taxonomy_state(
                portfolio_id,
                planning_taxonomy_id=planning_taxonomy_id,
                as_of_date=rebalance_date,
                frozen_taxonomy_node_ids=[],
                top_sleeve_weight_bounds=top_sleeve_weight_bounds,
                instrument_detail_cache=shared_detail_cache,
                direct_fx_instruments=shared_fx_instruments,
            )
            period_solution = solve_current_target_weights(
                portfolio_id,
                planning_taxonomy_id=planning_taxonomy_id,
                comparator_taxonomy_node_id=comparator_taxonomy_node_id,
                as_of_date=rebalance_date,
                lookback_days=lookback_days,
                calculation_frequency=calculation_frequency,
                target_dimension=target_dimension,
                capital_mode=capital_mode,
                gross_exposure=gross_exposure,
                target_volatility=target_volatility,
                max_gross_exposure=max_gross_exposure,
                missing_return_policy=missing_return_policy,
                frozen_taxonomy_node_ids=[],
                top_sleeve_weight_bounds=top_sleeve_weight_bounds,
                risk_model_config=risk_model_config,
                include_actuals=False,
                _resolve_frozen_actuals=False,
                _fixed_derivative_weight_override=derivative_reference_weight,
                _instrument_detail_cache=shared_detail_cache,
                _direct_fx_instruments=shared_fx_instruments,
            )
        except ValueError as error:
            if not _is_rebalance_data_gap_error(error):
                raise ValueError(f"{rebalance_date.isoformat()} rebalance failed: {error}") from error
            skipped_rebalance_dates.append(
                {"date": rebalance_date.isoformat(), "reason": str(error)}
            )
            warnings.append(
                f"{rebalance_date.isoformat()} rebalance skipped during point-in-time warm-up: {error}"
            )
            continue

        non_execution_ready_events = [
            event
            for event in list(period_solution.get("scope_solve_events") or [])
            if isinstance(event, dict) and event.get("execution_ready") is False
        ]
        if non_execution_ready_events:
            affected_scopes = ", ".join(
                dict.fromkeys(
                    f"{event.get('scope_label') or 'scope'} ({event.get('target_status') or 'target miss'})"
                    for event in non_execution_ready_events
                )
            )
            reason = (
                "Risk-budget constraints did not produce an execution-ready target"
                + (f": {affected_scopes}." if affected_scopes else ".")
            )
            skipped_rebalance_dates.append(
                {"date": rebalance_date.isoformat(), "reason": reason}
            )
            warnings.append(f"{rebalance_date.isoformat()} rebalance skipped: {reason}")
            continue

        target_weight_map = _backtest_target_weights(period_solution)
        missing_history = sorted(
            instrument_id
            for instrument_id in target_weight_map
            if instrument_id not in returns_by_instrument
        )
        if missing_history:
            raise ValueError(
                f"{rebalance_date.isoformat()} rebalance targets instruments without usable point-in-time history: "
                f"{', '.join(missing_history)}."
            )
        target_weights: list[dict[str, object]] = []
        for instrument_id, weight in sorted(target_weight_map.items()):
            top_id, top_label, top_path = _top_sleeve_for_member(
                decision_state,
                member_type=TARGET_MEMBER_INSTRUMENT,
                member_id=instrument_id,
            )
            target_weights.append(
                {
                    "instrument_id": instrument_id,
                    "target_weight": weight,
                    "top_sleeve_id": top_id,
                    "top_sleeve_label": top_label,
                    "top_sleeve_path": top_path,
                    "first_usable_observation_date": first_observation_by_instrument.get(
                        instrument_id
                    ),
                }
            )
        decisions.append(
            {
                "decision_date": rebalance_date.isoformat(),
                **(
                    {"implementation_delay_days": 0}
                    if rebalance_date in derivative_trigger_dates
                    else {}
                ),
                "taxonomy_configuration_version": period_solution.get(
                    "taxonomy_configuration_version"
                ),
                "taxonomy_configuration_effective_from": period_solution.get(
                    "taxonomy_configuration_effective_from"
                ),
                "target_weights": target_weights,
                "derivative_target_value": derivative_target_value,
                "derivative_reference_weight": derivative_reference_weight,
                "derivative_target_weight": sum(
                    float(row.get("target_weight") or 0.0)
                    for row in period_solution.get("leaf_targets", [])
                    if row.get("member_type") == TARGET_MEMBER_DERIVATIVE
                ),
            }
        )

    if not decisions:
        empty_backtest = _empty_point_in_time_backtest(
            rebalance_frequency=frequency,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            warnings=warnings,
            unavailable_reason=(
                "No scheduled rebalance produced an execution-ready target with sufficient "
                "point-in-time taxonomy and market history."
            ),
            methodology=methodology,
        )
        empty_backtest["point_in_time_coverage"]["skipped_rebalances"] = (
            skipped_rebalance_dates
        )
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
        }

    base_replay = _replay_backtest_decisions(
        decisions,
        returns_by_instrument=returns_by_instrument,
        as_of_date=as_of_date,
        cash_yield_annual=cash_yield_annual,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
        slippage_bps=slippage_bps,
        implementation_delay_days=implementation_delay_days,
        derivative_capital_events=list(derivative_context.get("events") or []),
        cash_borrowing_allowed=bool(
            methodology["assumptions"]["cash_borrowing_allowed"]
        ),
    )
    warnings.extend(list(base_replay.get("warnings") or []))
    skipped_rebalance_dates.extend(
        {
            "date": str(item.get("date") or ""),
            "reason": str(item.get("reason") or ""),
        }
        for item in list(base_replay.get("skipped_executions") or [])
        if isinstance(item, dict)
        and str(item.get("date") or "").strip()
        and str(item.get("reason") or "").strip()
    )
    points = list(base_replay.get("points") or [])
    portfolio_returns = dict(base_replay.get("returns") or {})

    robustness_results: list[dict[str, object]] = []
    base_metrics = _build_backtest_metrics(points, portfolio_returns)
    base_period_return = _safe_float(base_metrics.get("period_return"))
    seen_scenario_ids: set[str] = set()
    for scenario in robustness_scenarios or []:
        scenario_id = str((scenario or {}).get("scenario_id") or "").strip()
        if not scenario_id:
            raise ValueError("Every robustness scenario requires a scenario_id.")
        if scenario_id in seen_scenario_ids:
            raise ValueError(f"Duplicate robustness scenario_id: {scenario_id}.")
        seen_scenario_ids.add(scenario_id)
        scenario_inputs = {
            "cash_yield_annual": float(
                _safe_float((scenario or {}).get("cash_yield_annual")) or 0.0
            ),
            "commission_bps": float(
                _safe_float((scenario or {}).get("commission_bps")) or 0.0
            ),
            "tax_bps": float(_safe_float((scenario or {}).get("tax_bps")) or 0.0),
            "slippage_bps": float(
                _safe_float((scenario or {}).get("slippage_bps")) or 0.0
            ),
            "implementation_delay_days": int(
                _safe_float((scenario or {}).get("implementation_delay_days")) or 0
            ),
        }
        scenario_replay = _replay_backtest_decisions(
            decisions,
            returns_by_instrument=returns_by_instrument,
            as_of_date=as_of_date,
            derivative_capital_events=list(derivative_context.get("events") or []),
            cash_borrowing_allowed=bool(
                methodology["assumptions"]["cash_borrowing_allowed"]
            ),
            **scenario_inputs,
        )
        scenario_points = list(scenario_replay.get("points") or [])
        scenario_returns = dict(scenario_replay.get("returns") or {})
        scenario_metrics = _build_backtest_metrics(
            scenario_points, scenario_returns
        )
        scenario_period_return = _safe_float(scenario_metrics.get("period_return"))
        robustness_results.append(
            {
                "scenario_id": scenario_id,
                "label": str((scenario or {}).get("label") or scenario_id),
                **scenario_inputs,
                "metrics": scenario_metrics,
                "period_return_delta": (
                    scenario_period_return - base_period_return
                    if scenario_period_return is not None
                    and base_period_return is not None
                    else None
                ),
                "ending_value": (
                    _safe_float(scenario_points[-1].get("value"))
                    if scenario_points
                    else None
                ),
                "total_turnover": _safe_float(
                    scenario_replay.get("total_turnover")
                ),
                "total_cost": _safe_float(scenario_replay.get("total_cost")),
                "warnings": list(scenario_replay.get("warnings") or []),
            }
        )

    walk_forward = _build_walk_forward_validation(
        points,
        decisions,
        training_months=walk_forward_training_months,
        test_months=walk_forward_test_months,
    )

    comparison_payload = _build_backtest_benchmark_comparison_from_state(
        final_state,
        benchmark_instrument_id=benchmark_instrument_id,
        portfolio_points=points,
        portfolio_returns=portfolio_returns,
    )

    backtest = {
        "rebalance_frequency": frequency,
        "common_history_start_date": min(portfolio_first_dates).isoformat(),
        "start_date": points[0]["date"] if points else None,
        "end_date": points[-1]["date"] if points else as_of_date.isoformat(),
        "lookback_days": lookback_days,
        "points": points,
        "metrics": base_metrics,
        "top_sleeve_weight_points": list(
            base_replay.get("top_sleeve_weight_points") or []
        ),
        "top_sleeve_contribution_points": list(
            base_replay.get("top_sleeve_contribution_points") or []
        ),
        "contribution_reconciliation_points": list(
            base_replay.get("contribution_reconciliation_points") or []
        ),
        "execution_records": list(base_replay.get("execution_records") or []),
        "derivative_capital_events": list(
            base_replay.get("derivative_capital_events") or []
        ),
        "total_turnover": _safe_float(base_replay.get("total_turnover")) or 0.0,
        "total_cost": _safe_float(base_replay.get("total_cost")) or 0.0,
        "methodology": methodology,
        "point_in_time_coverage": {
            "status": "unavailable" if not points else ("complete" if not skipped_rebalance_dates else "partial"),
            "decision_count": len(decisions),
            "first_decision_date": decisions[0]["decision_date"],
            "last_decision_date": decisions[-1]["decision_date"],
            "configuration_versions_used": sorted(
                {
                    int(item["taxonomy_configuration_version"])
                    for item in decisions
                    if item.get("taxonomy_configuration_version") is not None
                }
            ),
            "historical_instrument_count": len(historical_instrument_ids),
            "first_usable_observation_by_instrument": first_observation_by_instrument,
            "skipped_rebalances": skipped_rebalance_dates,
            "pending_rebalances": list(base_replay.get("pending_executions") or []),
            "unavailable_reason": None if points else "No target decision could execute by the backtest cutoff.",
        },
        "robustness_results": robustness_results,
        "walk_forward": walk_forward,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return {
        "backtest": backtest,
        "backtest_benchmark": comparison_payload.get("backtest_benchmark"),
        "backtest_relative_metrics": comparison_payload.get("backtest_relative_metrics"),
    }


def solve_current_target_weights(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: str = "daily",
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    frozen_taxonomy_node_ids: list[str] | None = None,
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    risk_model_config: dict[str, object] | None = None,
    include_actuals: bool = True,
    _resolve_frozen_actuals: bool = False,
    _fixed_derivative_weight_override: float | None = None,
    _instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    _direct_fx_instruments: dict[tuple[str, str], str] | None = None,
) -> dict[str, object]:
    _validate_research_solve_configuration(
        calculation_frequency=calculation_frequency,
        target_dimension=target_dimension,
        capital_mode=capital_mode,
        gross_exposure=gross_exposure,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
    )
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        frozen_taxonomy_node_ids=frozen_taxonomy_node_ids,
        top_sleeve_weight_bounds=top_sleeve_weight_bounds,
        instrument_detail_cache=_instrument_detail_cache,
        direct_fx_instruments=_direct_fx_instruments,
    )
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    start_day = research_window_start_date(as_of_date, lookback_days)
    instrument_count = _scope_instrument_count(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        start_date=start_day,
        end_date=as_of_date,
    )
    frequency_profile = calculation_frequency_profile(
        instrument_count=instrument_count,
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
        missing_return_policy=_normalize_missing_return_policy(missing_return_policy),
        apply_capital_overlay=comparator_taxonomy_node_id is None,
        risk_model_config=risk_model_config,
        include_actuals=include_actuals,
        resolve_frozen_actuals=_resolve_frozen_actuals,
        fixed_derivative_weight_override=_fixed_derivative_weight_override,
    )
    if include_actuals:
        actual_rows, actual_warnings = _current_scope_actuals(
            state,
            scope_node_id=comparator_taxonomy_node_id,
            as_of_date=as_of_date,
        )
        scope_value_base = float(
            sum(_safe_float(row.get("current_value_base")) or 0.0 for row in actual_rows)
        )
        warnings = list(dict.fromkeys([*scope_result.warnings, *actual_warnings]))
        solved_result_groups, solved_result_warnings = _build_solved_result_groups(
            state,
            leaf_rows=scope_result.leaf_target_rows,
            member_rows=scope_result.member_target_rows,
            scope_value_base=scope_value_base,
            top_sleeve_bound_weight_by_id=scope_result.top_sleeve_bound_weight_by_id,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=resolved_calculation_frequency,  # type: ignore[arg-type]
            missing_return_policy=_normalize_missing_return_policy(missing_return_policy),
            risk_model_config=risk_model_config,
        )
        warnings = list(dict.fromkeys([*warnings, *solved_result_warnings]))
        target_weight_gaps = _build_leaf_target_weight_gaps(
            leaf_target_rows=scope_result.leaf_target_rows,
            base_currency=state.base_currency,
            scope_value_base=scope_value_base,
            instrument_research_state_by_id={
                str(item.get("instrument_id") or ""): item
                for item in list_portfolio_instrument_universe(portfolio_id)
                if str(item.get("instrument_id") or "")
            },
        )
    else:
        actual_rows = []
        scope_value_base = None
        warnings = list(dict.fromkeys(scope_result.warnings))
        solved_result_groups = []
        target_weight_gaps = []
    return {
        "portfolio_id": portfolio_id,
        "planning_taxonomy_id": planning_taxonomy_id,
        "planning_taxonomy_name": state.taxonomy_name,
        "taxonomy_configuration_version": state.configuration_version,
        "taxonomy_configuration_effective_from": (
            state.configuration_effective_from.isoformat()
            if state.configuration_effective_from is not None
            else None
        ),
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
        "solved_result_groups": solved_result_groups,
        "actual_rows": deepcopy(actual_rows),
        "resolved_target_rows": deepcopy(scope_result.resolved_target_rows),
        "solve_event": deepcopy(scope_result.solve_event),
        "scope_solve_events": deepcopy(scope_result.scope_solve_events),
        "target_weight_gaps": target_weight_gaps,
        "warnings": warnings,
        "return_observations": float(len(scope_result.return_series)),
        "calculation_frequency": frequency_profile,
    }
