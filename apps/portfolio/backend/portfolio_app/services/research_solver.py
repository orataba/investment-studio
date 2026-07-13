from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from math import ceil, sqrt

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_ops_instrument_core import (
    CanonicalQuoteResolution,
    CanonicalQuoteSeriesPoint,
    CanonicalQuoteSeriesResolution,
    resolve_quote_series_observation_at,
)
from portfolio_ops_instrument_core.canonical_fx import CanonicalFxWindowBook
from portfolio_ops_instrument_core.db_models import Instrument
from portfolio_ops_instrument_core.models import TOTAL_RETURN_QUOTE_BASES

from portfolio_app.db.session import get_session_factory
from portfolio_app.services.fact_currency import require_portfolio_fact_currency
from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
    infer_observation_frequency,
    period_end_date,
)
from portfolio_app.services.canonical_fx import (
    portfolio_fx_freshness_policy,
    resolve_portfolio_fx_window_book_in_session,
)
from portfolio_app.services.canonical_quotes import (
    CanonicalQuoteWindowBook,
    resolve_role_quote_window_book_in_session,
)
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.performance import build_holdings_report
from portfolio_app.services.performance_reliability import (
    build_performance_history_reliability,
)
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
SYSTEM_CASH_TARGET_MEMBER_ID = "__cash__"
SYSTEM_CASH_TARGET_LABEL = "Cash"
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
    "weekly": 4.0,
    "monthly": 1.0,
}
RESEARCH_RISK_CONTRIBUTION_MODE = "signed"
RESEARCH_MAX_RISK_BUDGET_SHARE_GAP = 1e-4
RESEARCH_MARKET_DATA_POLICY_VERSION = "allocation_research_market_data.v1"
RESEARCH_COVARIANCE_PSD_TOLERANCE = 1e-10
RISK_BUDGET_NORMALIZED_GAP_FLOOR_EQUAL_SHARE_FRACTION = 0.25
RISK_BUDGET_NORMALIZED_GAP_MAX_FLOOR = 0.05
ABS_RC_SMOOTHING_EPS = 1e-12
MISSING_RETURN_POLICY_STRICT = "strict"
MISSING_RETURN_POLICY_COMPLETE_CASE_DROP = "complete_case_drop"
RESEARCH_DEFAULT_MISSING_RETURN_POLICY = MISSING_RETURN_POLICY_STRICT
RESEARCH_COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION = 0.10
RESEARCH_COMPLETE_CASE_DROP_MAX_TRAILING_STALENESS_DAYS: dict[CalculationFrequency, int] = {
    "daily": 5,
    "weekly": 14,
    "monthly": 62,
}
SUPPORTED_RESEARCH_LOOKBACK_DAYS = frozenset(RESEARCH_WINDOW_MONTHS_BY_LOOKBACK_DAYS)
RESEARCH_BACKTEST_METHODOLOGY_WARNINGS: tuple[str, ...] = (
    "Backtest applies the currently configured taxonomy membership and target policy across the full historical simulation; it is not a point-in-time reconstruction of past classifications or mandates.",
    "Backtest cash residual earns a 0% return, and simulated returns exclude transaction costs, taxes, slippage, and implementation delay.",
)
RESEARCH_BACKTEST_METRICS_METHOD_VERSION = (
    "research-backtest-metrics.v2.history-gated-arithmetic-sharpe"
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
    dropped_return_rows: list[dict[str, object]] | None = None
    latest_complete_return_date: str | None = None
    trailing_complete_return_staleness_days: int | None = None


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
    frozen_taxonomy_node_ids: frozenset[str]
    top_sleeve_weight_bounds: dict[str, dict[str, float | None]]
    market_data: "ResearchMarketDataContext | None" = field(
        default=None,
        repr=False,
        compare=False,
    )
    # A current-target solve walks the taxonomy recursively.  Current holdings
    # and account values are portfolio-level inputs, so rebuilding both ledgers
    # once per scope is redundant and can make deep taxonomies disproportionately
    # expensive.  Keep the lazy valuation snapshot on the per-solve state; a new
    # state is constructed for every as-of date (including each backtest
    # rebalance), so this cache never crosses a point-in-time boundary.
    current_valuation_cache: dict[str, object] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class ResearchMarketDataContext:
    policy_version: str
    base_currency: str
    start_date: date
    end_date: date
    instrument_ids: frozenset[str]
    instrument_name_by_id: dict[str, str]
    instrument_currency_by_id: dict[str, str]
    total_return_book: CanonicalQuoteWindowBook
    fx_book: CanonicalFxWindowBook
    adopted_quote_dependencies: dict[str, dict[str, object]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    adopted_fx_dependencies: dict[str, dict[str, object]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


class ResearchMarketDataError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason_codes: list[str],
        dependency: dict[str, object],
    ) -> None:
        normalized_reasons = list(dict.fromkeys(reason_codes or ["unavailable_dependency"]))
        super().__init__(f"{message} [reason_codes={','.join(normalized_reasons)}]")
        self.reason_codes = tuple(normalized_reasons)
        self.dependency = dependency


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


def _normalized_currency(value: object, *, context: str) -> str:
    return require_portfolio_fact_currency(value, context=context)


def _research_instrument_ids(
    state: TaxonomyResearchState,
    *,
    scope_node_id: str | None = None,
    additional_instrument_ids: list[str] | None = None,
) -> list[str]:
    node_ids = (
        set(state.node_by_id)
        if scope_node_id is None
        else state.node_subtree_by_id.get(scope_node_id, {scope_node_id})
    )
    instrument_ids = {
        str(assignment.get("target_entity_id") or "").strip()
        for node_id in node_ids
        for assignment in state.direct_assignments_by_node.get(node_id, [])
        if str(assignment.get("target_scope") or "") == TARGET_MEMBER_INSTRUMENT
    }
    instrument_ids.update(
        str(instrument_id or "").strip()
        for instrument_id in (additional_instrument_ids or [])
    )
    return sorted(instrument_id for instrument_id in instrument_ids if instrument_id)


def _lock_research_market_data_in_session(
    session: Session,
    *,
    instrument_ids: list[str],
    base_currency: str,
    start_date: date,
    end_date: date,
) -> ResearchMarketDataContext:
    normalized_ids = sorted(
        {str(instrument_id or "").strip() for instrument_id in instrument_ids}
        - {""}
    )
    normalized_base_currency = _normalized_currency(
        base_currency,
        context="Research market-data base",
    )
    metadata_rows = session.execute(
        select(
            Instrument.instrument_id,
            Instrument.instrument_name,
            Instrument.currency,
        ).where(Instrument.instrument_id.in_(normalized_ids))
    ).all() if normalized_ids else []
    instrument_name_by_id = {
        str(instrument_id): str(instrument_name)
        for instrument_id, instrument_name, _currency in metadata_rows
    }
    instrument_currency_by_id = {
        str(instrument_id): _normalized_currency(
            currency,
            context=f"Research instrument '{instrument_id}'",
        )
        for instrument_id, _instrument_name, currency in metadata_rows
    }
    total_return_book = resolve_role_quote_window_book_in_session(
        session,
        instrument_ids=normalized_ids,
        role="total_return",
        start_date=start_date,
        end_date=end_date,
    )
    currency_pairs = {
        (currency, normalized_base_currency)
        for currency in instrument_currency_by_id.values()
        if currency
    }
    currency_pairs.add((normalized_base_currency, normalized_base_currency))
    fx_book = resolve_portfolio_fx_window_book_in_session(
        session,
        currency_pairs=sorted(currency_pairs),
        start_date=start_date,
        end_date=end_date,
        freshness_policy=portfolio_fx_freshness_policy(),
    )
    return ResearchMarketDataContext(
        policy_version=RESEARCH_MARKET_DATA_POLICY_VERSION,
        base_currency=normalized_base_currency,
        start_date=start_date,
        end_date=end_date,
        instrument_ids=frozenset(normalized_ids),
        instrument_name_by_id=instrument_name_by_id,
        instrument_currency_by_id=instrument_currency_by_id,
        total_return_book=total_return_book,
        fx_book=fx_book,
    )


def lock_research_market_data_in_session(
    session: Session,
    *,
    instrument_ids: list[str],
    base_currency: str,
    start_date: date,
    end_date: date,
) -> ResearchMarketDataContext:
    """Public one-session canonical market-data boundary for risk consumers."""

    return _lock_research_market_data_in_session(
        session,
        instrument_ids=instrument_ids,
        base_currency=base_currency,
        start_date=start_date,
        end_date=end_date,
    )


def _lock_research_market_data(
    *,
    instrument_ids: list[str],
    base_currency: str,
    start_date: date,
    end_date: date,
) -> ResearchMarketDataContext:
    session_factory = get_session_factory()
    with session_factory() as session:
        return _lock_research_market_data_in_session(
            session,
            instrument_ids=instrument_ids,
            base_currency=base_currency,
            start_date=start_date,
            end_date=end_date,
        )


def _state_with_locked_market_data(
    state: TaxonomyResearchState,
    *,
    instrument_ids: list[str],
    start_date: date,
    end_date: date,
) -> TaxonomyResearchState:
    context = _lock_research_market_data(
        instrument_ids=instrument_ids,
        base_currency=state.base_currency,
        start_date=start_date,
        end_date=end_date,
    )
    return replace(state, market_data=context)


def _require_research_market_data(
    state: TaxonomyResearchState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
) -> ResearchMarketDataContext:
    context = state.market_data
    if context is None:
        raise ResearchMarketDataError(
            "Allocation Research requires a pre-locked canonical market-data context.",
            reason_codes=["missing_research_market_data_context"],
            dependency={
                "policy_version": RESEARCH_MARKET_DATA_POLICY_VERSION,
                "instrument_id": instrument_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
    if start_date < context.start_date or end_date > context.end_date:
        raise ResearchMarketDataError(
            f"{instrument_id} requested dates fall outside the locked research window.",
            reason_codes=["research_window_not_locked"],
            dependency={
                "policy_version": context.policy_version,
                "instrument_id": instrument_id,
                "locked_start_date": context.start_date.isoformat(),
                "locked_end_date": context.end_date.isoformat(),
                "requested_start_date": start_date.isoformat(),
                "requested_end_date": end_date.isoformat(),
            },
        )
    if instrument_id not in context.instrument_ids:
        raise ResearchMarketDataError(
            f"{instrument_id} was not included in the locked research input set.",
            reason_codes=["research_instrument_not_locked"],
            dependency={
                "policy_version": context.policy_version,
                "instrument_id": instrument_id,
                "locked_instrument_ids": sorted(context.instrument_ids),
            },
        )
    return context


def _quote_dependency_payload(
    *,
    context: ResearchMarketDataContext,
    instrument_id: str,
    window: CanonicalQuoteSeriesResolution | None,
    endpoint: CanonicalQuoteResolution | None,
    non_complete_observations: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "policy_version": context.policy_version,
        "instrument_id": instrument_id,
        "role": "total_return",
        "window_calculation_dependency": (
            window.calculation_dependency.model_dump(mode="json")
            if window is not None
            else None
        ),
        "endpoint_calculation_dependency": (
            endpoint.calculation_dependency.model_dump(mode="json")
            if endpoint is not None
            else None
        ),
        "non_complete_observations": non_complete_observations,
    }


def _resolved_total_return_points(
    state: TaxonomyResearchState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
) -> tuple[
    ResearchMarketDataContext,
    CanonicalQuoteSeriesResolution,
    CanonicalQuoteResolution,
    list[CanonicalQuoteSeriesPoint],
]:
    context = _require_research_market_data(
        state,
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
    )
    window = context.total_return_book.windows.get(instrument_id)
    if window is None:
        reason_code = (
            "instrument_not_found"
            if instrument_id in context.total_return_book.missing_instrument_ids
            else "missing_quote_series"
        )
        dependency = _quote_dependency_payload(
            context=context,
            instrument_id=instrument_id,
            window=None,
            endpoint=None,
            non_complete_observations=[],
        )
        raise ResearchMarketDataError(
            f"{instrument_id} has no locked canonical total-return window.",
            reason_codes=[reason_code],
            dependency=dependency,
        )
    identity_reasons: list[str] = []
    if window.role != "total_return":
        identity_reasons.append("invalid_quote_role")
    if window.quote_series_id is None:
        identity_reasons.extend(window.reason_codes or ["missing_quote_series"])
    elif window.quote_basis not in TOTAL_RETURN_QUOTE_BASES:
        identity_reasons.append("invalid_total_return_quote_basis")
    endpoint: CanonicalQuoteResolution | None = None
    if not identity_reasons:
        try:
            endpoint = resolve_quote_series_observation_at(
                window,
                requested_as_of_date=end_date,
            )
        except ValueError as error:
            identity_reasons.append(
                str(getattr(error, "reason_code", "unavailable_total_return_endpoint"))
            )
    non_complete_observations = [
        {
            "observation_date": observation.observation_date.isoformat(),
            "status": observation.status,
            "observation_id": observation.observation_id,
            "revision_id": observation.revision_id,
            "revision_number": observation.revision_number,
            "payload_hash": observation.payload_hash,
        }
        for observation in window.observations
        if start_date <= observation.observation_date <= end_date
        and observation.status != "complete"
    ]
    reason_by_status = {
        "partial": "partial_series",
        "rejected": "rejected_observation",
        "withdrawn": "withdrawn_observation",
    }
    failure_reasons = list(identity_reasons)
    if endpoint is not None and endpoint.resolution_status != "resolved":
        failure_reasons.extend(endpoint.reason_codes or ["unavailable_total_return_endpoint"])
    failure_reasons.extend(
        reason_by_status.get(str(item["status"]), "unavailable_observation")
        for item in non_complete_observations
    )
    dependency = _quote_dependency_payload(
        context=context,
        instrument_id=instrument_id,
        window=window,
        endpoint=endpoint,
        non_complete_observations=non_complete_observations,
    )
    dependency_key = (
        endpoint.calculation_dependency.fingerprint
        if endpoint is not None
        else window.calculation_dependency.fingerprint
    )
    context.adopted_quote_dependencies[dependency_key] = dependency
    if failure_reasons or endpoint is None:
        raise ResearchMarketDataError(
            f"{instrument_id} canonical total-return dependency is unavailable.",
            reason_codes=failure_reasons or ["unavailable_total_return_endpoint"],
            dependency=dependency,
        )
    points = [
        point
        for point in window.points
        if start_date <= point.observation_date <= end_date
    ]
    if not points:
        raise ResearchMarketDataError(
            f"{instrument_id} has no canonical total-return observations in the requested window.",
            reason_codes=["insufficient_history"],
            dependency=dependency,
        )
    return context, window, endpoint, points


def _research_market_data_manifest(
    state: TaxonomyResearchState,
) -> dict[str, object] | None:
    context = state.market_data
    if context is None:
        return None
    return research_market_data_manifest(context)


def research_market_data_manifest(
    context: ResearchMarketDataContext,
) -> dict[str, object]:
    """Render adopted canonical quote/FX dependencies without re-resolving them."""

    return {
        "policy_version": context.policy_version,
        "base_currency": context.base_currency,
        "start_date": context.start_date.isoformat(),
        "end_date": context.end_date.isoformat(),
        "instrument_ids": sorted(context.instrument_ids),
        "quote_dependencies": [
            context.adopted_quote_dependencies[key]
            for key in sorted(context.adopted_quote_dependencies)
        ],
        "fx_dependencies": [
            context.adopted_fx_dependencies[key]
            for key in sorted(context.adopted_fx_dependencies)
        ],
    }


@dataclass(frozen=True)
class _CanonicalSeriesState:
    """Narrow adapter that keeps canonical series construction in one kernel."""

    base_currency: str
    market_data: ResearchMarketDataContext


def _build_instrument_nav_series(
    state: TaxonomyResearchState,
    *,
    instrument_id: str,
    start_date: date,
    end_date: date,
    warn_on_start_clip: bool = True,
) -> tuple[pd.Series, list[str]]:
    context, window, endpoint, selected_points = _resolved_total_return_points(
        state,
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
    )
    warnings: list[str] = []
    rows: dict[date, Decimal] = {}
    for point in selected_points:
        fx_resolution = context.fx_book.rate_at(
            window.currency,
            state.base_currency,
            point.observation_date,
        )
        fx_dependency = fx_resolution.calculation_dependency.model_dump(mode="json")
        context.adopted_fx_dependencies[
            fx_resolution.calculation_dependency.fingerprint
        ] = fx_dependency
        if fx_resolution.resolution_status != "resolved" or fx_resolution.rate is None:
            raise ResearchMarketDataError(
                f"{instrument_id} canonical FX dependency is unavailable on "
                f"{point.observation_date.isoformat()}.",
                reason_codes=list(fx_resolution.reason_codes or ["unavailable_fx_dependency"]),
                dependency={
                    "policy_version": context.policy_version,
                    "instrument_id": instrument_id,
                    "quote_observation_id": point.observation_id,
                    "quote_revision_id": point.revision_id,
                    "fx_calculation_dependency": fx_dependency,
                },
            )
        rows[point.observation_date] = point.value * fx_resolution.rate
    if not rows:
        raise ResearchMarketDataError(
            f"{instrument_id} has no FX-complete canonical total-return history.",
            reason_codes=["insufficient_history"],
            dependency={
                "policy_version": context.policy_version,
                "instrument_id": instrument_id,
                "quote_calculation_dependency": endpoint.calculation_dependency.model_dump(
                    mode="json"
                ),
            },
        )
    visible = pd.Series(
        {point_date: float(value) for point_date, value in rows.items()},
        dtype="float64",
    ).sort_index()
    if warn_on_start_clip and visible.index[0] > start_date:
        warnings.append(
            f"{instrument_id} history starts on {visible.index[0].isoformat()}, so the research window is clipped for this member."
        )
    if endpoint.reliability_status == "qualified":
        warnings.append(
            f"{instrument_id} total-return endpoint is qualified under the explicit canonical freshness policy."
        )
    return visible, warnings


def build_canonical_total_return_nav_series(
    context: ResearchMarketDataContext,
    *,
    instrument_id: str,
    base_currency: str,
    start_date: date,
    end_date: date,
    warn_on_start_clip: bool = True,
) -> tuple[pd.Series, list[str]]:
    """Build FX-complete canonical total-return NAV through the research kernel."""

    state = _CanonicalSeriesState(
        base_currency=_normalized_currency(
            base_currency,
            context=f"Research series '{instrument_id}' base",
        ),
        market_data=context,
    )
    return _build_instrument_nav_series(  # type: ignore[arg-type]
        state,
        instrument_id=instrument_id,
        start_date=start_date,
        end_date=end_date,
        warn_on_start_clip=warn_on_start_clip,
    )


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


def _taxonomy_node_row_is_system_cash_like(node: dict[str, object]) -> bool:
    normalized_name = str(node.get("node_name") or "").strip().lower()
    normalized_code = str(node.get("node_code") or "").strip().lower()
    return normalized_code == "cash" or normalized_name in {"cash", "现金"}


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
    start_day = research_window_start_date(pd.Timestamp(end_date).date(), lookback_days)
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
        if missing_count and missing_fraction > RESEARCH_COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION + 1e-12:
            raise ValueError(
                f"{label} complete-case drop would remove {missing_count} of {len(returns)} return rows "
                f"({missing_fraction:.2%}), exceeding the "
                f"{RESEARCH_COMPLETE_CASE_DROP_MAX_MISSING_ROW_FRACTION:.2%} limit. "
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
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
    bounds = list(zip(problem.lower_bounds.tolist(), problem.upper_bounds.tolist()))
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

    bounds = list(zip(problem.lower_bounds.tolist(), problem.upper_bounds.tolist())) + [(0.0, None)]
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
        initial_guesses=[*initial_guesses, solution.weights],
        max_normalized_gap_ceiling=max_normalized_share_gap,
        max_iterations=max_iterations,
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
    bounds = list(zip(problem.lower_bounds.tolist(), problem.upper_bounds.tolist()))
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
    if _risk_budget_problem_has_binding_bounds(problem):
        minimax = _solve_minimax_risk_budget_slsqp(
            problem,
            reference_weights=reference_weights,
            initial_guesses=initial_guesses,
            max_iterations=max(max_iterations * 4, 1500),
        )
        if minimax is None:
            raise ValueError("Risk budget bounded minimax solver failed.")
        best = minimax
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
    if enforce_tolerance and best.max_abs_share_gap > RESEARCH_MAX_RISK_BUDGET_SHARE_GAP + 1e-12:
        raise ValueError(
            "Risk budget solver could not satisfy target risk shares within "
            f"{RESEARCH_MAX_RISK_BUDGET_SHARE_GAP:.2%}; achieved max gap {best.max_abs_share_gap:.2%}."
        )
    achieved = np.asarray(best.achieved_risk_shares, dtype="float64")
    if (
        best.contribution_mode == "signed"
        and not _risk_budget_problem_has_binding_bounds(problem)
        and float(achieved.min()) < -1e-12
    ):
        raise ValueError("Risk budget solver produced a negative signed risk share.")
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
    count = len(target_shares)
    normalized_missing_return_policy = _normalize_missing_return_policy(missing_return_policy)
    resolved_lower_bounds = (
        np.asarray(lower_bounds, dtype="float64")
        if lower_bounds is not None and len(lower_bounds) == count
        else np.zeros(count, dtype="float64")
    )
    resolved_upper_bounds = (
        np.asarray(upper_bounds, dtype="float64")
        if upper_bounds is not None and len(upper_bounds) == count
        else np.ones(count, dtype="float64")
    )
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
    )
    covariance_window = coverage.returns

    target = _normalize_positive_vector(np.asarray(target_shares, dtype="float64"))
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
        dropped_return_rows=coverage.dropped_rows,
        latest_complete_return_date=coverage.latest_complete_date.isoformat() if coverage.latest_complete_date else None,
        trailing_complete_return_staleness_days=coverage.trailing_staleness_days,
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
    return min(target_gross, max_gross)


def _series_observation_frequency(series: pd.Series) -> CalculationFrequency:
    return infer_observation_frequency(_index_dates(series.index))


def _max_period_staleness_days(calculation_frequency: CalculationFrequency) -> int:
    return int(_research_covariance_parameters(calculation_frequency).get("max_period_staleness_days", 0))


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
                    f"{calculation_frequency.title()} research alignment found a stale observation: "
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
    """Use the research engine's period-end alignment and staleness policy."""

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
    line_keys = [(member.member_type, member.member_id) for member in scope_members]

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
        for member in scope_members:
            line = line_map.get((member.member_type, member.member_id))
            if line is None or line.get(value_field) is None:
                if member.member_type != TARGET_MEMBER_CASH:
                    missing_member_labels.append(member.label)
                    complete = False
                    break
                selected_value = 0.0
                target_weight = 0.0 if resolved_dimension == TARGET_DIMENSION_WEIGHT else _safe_float((line or {}).get("target_weight"))
                target_risk_share = (
                    0.0
                    if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET
                    else _safe_float((line or {}).get("target_risk_share"))
                )
            else:
                selected_value = float(line.get(value_field))
                target_weight = _safe_float(line.get("target_weight"))
                target_risk_share = _safe_float(line.get("target_risk_share"))
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
        if not complete:
            incomplete_target_sets.append((target_set, missing_member_labels))

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
    if saw_enabled_target_set and incomplete_target_sets:
        target_set, missing_member_labels = incomplete_target_sets[0]
        missing_label = ", ".join(missing_member_labels[:8]) or "one or more active taxonomy members"
        if len(missing_member_labels) > 8:
            missing_label += f", +{len(missing_member_labels) - 8} more"
        raise ValueError(
            f"{scope_label} {resolved_dimension} target set is incomplete; "
            f"missing target lines for active members: {missing_label}."
        )
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
        if target_scope == TARGET_MEMBER_INSTRUMENT:
            label = (
                state.market_data.instrument_name_by_id.get(target_entity_id, target_entity_id)
                if state.market_data is not None
                else target_entity_id
            )
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
            try:
                _context, _window, _endpoint, points = _resolved_total_return_points(
                    state,
                    instrument_id=instrument_id,
                    start_date=start_date,
                    end_date=end_date,
                )
            except ResearchMarketDataError:
                continue
            dates = [
                point.observation_date
                for point in points
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
    return gross_returns.cumprod(skipna=False)


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
        and not _member_is_cash_like(
            state,
            member_by_key[f"{row['member_type']}::{row['member_id']}"],
        )
        and not _member_is_frozen(
            state,
            scope_node_id=scope_node_id,
            member=member_by_key[f"{row['member_type']}::{row['member_id']}"],
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
        if member_key in zero_target_keys:
            zero_nav = _build_cash_nav_series(start_date=start_day, end_date=as_of_date)
            nav_series_by_member[(member.member_type, member.member_id)] = zero_nav
            current_nav_series_by_member[(member.member_type, member.member_id)] = zero_nav
            continue
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
                missing_return_policy=missing_return_policy,
                apply_capital_overlay=False,
                risk_model_config=risk_model_config,
                include_actuals=include_actuals,
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

    if include_actuals:
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
    else:
        current_actual_weight_by_key = {}

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
    risk_keys = [
        key
        for key in member_keys
        if key not in cash_like_keys and key not in frozen_keys and key not in zero_target_keys
    ]
    non_cash_keys = [key for key in member_keys if key not in cash_like_keys]
    preferred_cash_weights = pd.Series(
        {
            f"{row['member_type']}::{row['member_id']}": float(_safe_float(row.get("target_weight")) or 0.0)
            for row in resolved_rows
            if f"{row['member_type']}::{row['member_id']}" in cash_like_keys
        },
        dtype="float64",
    ).reindex(cash_like_keys, fill_value=0.0)
    fixed_total = max(float(fixed_weight_targets.sum()), 0.0)
    overlay_applies_to_risk_sleeves = apply_capital_overlay and capital_mode in {
        CAPITAL_MODE_FIXED_GROSS,
        CAPITAL_MODE_TARGET_VOLATILITY,
        CAPITAL_MODE_VOLATILITY_CAP,
    }
    fixed_gross_overlay = overlay_applies_to_risk_sleeves and capital_mode == CAPITAL_MODE_FIXED_GROSS
    target_non_cash_total = float(gross_exposure or 1.0) if fixed_gross_overlay else None

    if target_dimension_used == TARGET_DIMENSION_RISK_BUDGET:
        base_cash_total = 0.0 if fixed_gross_overlay else min(max(float(preferred_cash_weights.sum()), 0.0), 1.0)
        available_non_cash_total = (
            float(target_non_cash_total)
            if target_non_cash_total is not None
            else max(1.0 - base_cash_total, 0.0)
        )
        if fixed_total > available_non_cash_total + 1e-12:
            raise ValueError(
                f"{scope_label} frozen sleeve weights require {fixed_total:.2%}, "
                f"above the available {available_non_cash_total:.2%} non-cash budget."
            )
        fixed_total = min(fixed_total, available_non_cash_total)
        _validate_fixed_top_sleeve_bounds(
            scope_label=scope_label,
            fixed_weight_targets=fixed_weight_targets,
            bounds_by_key=top_sleeve_bounds_by_key,
            member_by_key=member_by_key,
        )
        active_budget, lower_bounds, upper_bounds = _resolve_active_top_sleeve_bound_vectors(
            scope_label=scope_label,
            active_keys=risk_keys,
            active_budget=max(available_non_cash_total - fixed_total, 0.0),
            bounds_by_key=top_sleeve_bounds_by_key,
            member_by_key=member_by_key,
            allow_upper_shortfall=not fixed_gross_overlay,
        )
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
            risk_solve = _solve_risk_budget_weights(
                target_shares=target_values.reindex(risk_keys, fill_value=0.0).to_numpy(dtype="float64"),
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
            solved_weights = risk_solve.weights
            risk_gap = risk_solve.max_abs_share_gap
            solver_kind = risk_solve.solver_kind
            implementation_weights = pd.Series(0.0, index=member_keys, dtype="float64")
            implementation_weights.loc[frozen_keys] = fixed_weight_targets.reindex(frozen_keys, fill_value=0.0)
            implementation_weights.loc[risk_keys] = solved_weights * active_budget
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
        active_weight_keys = [
            key
            for key in member_keys
            if key not in cash_like_keys and key not in frozen_keys and key not in zero_target_keys
        ]
        active_weight_targets = target_values.reindex(active_weight_keys, fill_value=0.0)
        active_weight_total = float(active_weight_targets.sum())
        available_non_cash_total = (
            float(target_non_cash_total)
            if target_non_cash_total is not None
            else max(1.0 - float(preferred_cash_weights.sum()), 0.0)
        )
        if float(implementation_weights.loc[frozen_keys].sum()) > available_non_cash_total + 1e-12:
            raise ValueError(
                f"{scope_label} frozen sleeve weights require {float(implementation_weights.loc[frozen_keys].sum()):.2%}, "
                f"above the available {available_non_cash_total:.2%} non-cash budget."
            )
        requested_active_budget = max(
            available_non_cash_total - float(implementation_weights.loc[frozen_keys].sum()),
            0.0,
        )
        _validate_fixed_top_sleeve_bounds(
            scope_label=scope_label,
            fixed_weight_targets=fixed_weight_targets,
            bounds_by_key=top_sleeve_bounds_by_key,
            member_by_key=member_by_key,
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
        implementation_weights.loc[cash_like_keys] = _allocate_cash_weights(
            cash_index=cash_like_keys,
            preferred_weights=preferred_cash_weights,
            total_cash_weight=1.0
            - float(implementation_weights.loc[active_weight_keys].sum())
            - float(implementation_weights.loc[frozen_keys].sum()),
        )
        risk_gap = None
        solver_kind = "weight-fixed-members" if frozen_keys else "weight"

    if overlay_applies_to_risk_sleeves and not fixed_gross_overlay and non_cash_keys:
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

    top_sleeve_bound_weights = implementation_weights.copy()
    estimated_risk_sleeve_volatility = None
    effective_gross_exposure = None
    risky_allocation_scaling_factor = None
    if overlay_applies_to_risk_sleeves and non_cash_keys:
        risky_weights = implementation_weights.reindex(non_cash_keys, fill_value=0.0)
        if capital_mode == CAPITAL_MODE_FIXED_GROSS:
            effective_gross_exposure = float(risky_weights.sum())
            risky_allocation_scaling_factor = 1.0
        elif capital_mode in {CAPITAL_MODE_TARGET_VOLATILITY, CAPITAL_MODE_VOLATILITY_CAP}:
            active_risky_weights = risky_weights.loc[risky_weights.abs() > 1e-12]
            if active_risky_weights.empty:
                raise ValueError(
                    f"{scope_label} volatility overlay requires at least one non-zero risky-sleeve weight."
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
                effective_gross_exposure = _resolve_volatility_overlay_gross_exposure(
                    capital_mode=capital_mode,
                    estimated_volatility=estimated_risk_sleeve_volatility,
                    target_volatility=target_volatility,
                    max_gross_exposure=max_gross_exposure,
                )
            except ValueError as error:
                raise ValueError(
                    f"{scope_label} {str(error).removeprefix('Volatility overlay ')}"
                ) from error
        if effective_gross_exposure is not None and not fixed_gross_overlay:
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
                    raise ValueError(
                        f"{scope_label} capital overlay leaves a {residual_cash:.2%} residual but the scope has no cash-like member."
                    )

    top_sleeve_bound_status_weights = (
        top_sleeve_bound_weights
        if capital_mode == CAPITAL_MODE_VOLATILITY_CAP
        else implementation_weights
    )
    _validate_final_top_sleeve_bounds(
        scope_label=scope_label,
        implementation_weights=top_sleeve_bound_status_weights,
        bounds_by_key=top_sleeve_bounds_by_key,
        member_by_key=member_by_key,
    )
    if fixed_gross_overlay and not cash_like_keys:
        residual_cash = 1.0 - float(implementation_weights.reindex(non_cash_keys, fill_value=0.0).sum())
        if abs(residual_cash) > 1e-9:
            raise ValueError(
                f"{scope_label} fixed gross leaves a {residual_cash:.2%} residual but the scope has no cash-like member."
            )

    for row in resolved_rows:
        member_key = f"{row['member_type']}::{row['member_id']}"
        row["implementation_weight"] = float(implementation_weights.get(member_key, 0.0))
    top_sleeve_bound_weight_by_id = {
        member_by_key[key].member_id: float(top_sleeve_bound_status_weights.get(key, 0.0))
        for key in top_sleeve_bounds_by_key
        if key in member_by_key
    }

    current_weights = pd.Series(current_actual_weight_by_key, dtype="float64").reindex(member_keys, fill_value=0.0)
    if include_actuals:
        current_risk_share_by_key, current_risk_share_warnings = _estimate_scope_risk_share_map(
            members=members,
            nav_series_by_member=current_nav_series_by_member,
            risk_keys=non_cash_keys,
            weights_by_key=current_weights,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            calculation_frequency=calculation_frequency,
            missing_return_policy=missing_return_policy,
            contribution_mode=risk_solve.risk_contribution_mode or RESEARCH_RISK_CONTRIBUTION_MODE,
            risk_model_config=risk_model_config,
        )
        warnings.extend(current_risk_share_warnings)
    else:
        current_risk_share_by_key = {}
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
        "missing_return_policy": risk_solve.missing_return_policy or _normalize_missing_return_policy(missing_return_policy),
        "return_rows_before_policy": risk_solve.return_rows_before_policy,
        "return_rows_after_policy": risk_solve.return_rows_after_policy,
        "missing_return_row_count": risk_solve.missing_return_row_count,
        "missing_return_row_fraction": risk_solve.missing_return_row_fraction,
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
                market_value_base = _safe_float(position.get("market_value_base"))
                if market_value_base is None:
                    raise ValueError(
                        "Current allocation valuation is incomplete; refresh price and FX coverage before solving."
                    )
                position_value_by_instrument[instrument_id] = market_value_base

        visible_cash_accounts = [
            account_row
            for account_row in list(account_workspace.get("accounts") or [])
            if str((account_row.get("account") or {}).get("account_type") or "") == "deposit_account"
        ]
        if any(_safe_float(account_row.get("account_value_base")) is None for account_row in visible_cash_accounts):
            raise ValueError(
                "Current cash valuation is incomplete; refresh FX coverage before solving."
            )
        cash_value_by_account = {
            str((account_row.get("account") or {}).get("account_id") or ""): float(account_row["account_value_base"])
            for account_row in visible_cash_accounts
            if str((account_row.get("account") or {}).get("account_id") or "")
        }
        state.current_valuation_cache[cache_key] = {
            "position_value_by_instrument": dict(position_value_by_instrument),
            "cash_value_by_account": dict(cash_value_by_account),
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
        sum(node_value_map.get(node_id, 0.0) for node_id in state.children_by_parent.get(None, [])) + cash_total_value + unassigned_value
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
    for member in scope_members:
        if member.member_type == TARGET_MEMBER_NODE:
            actual_value = node_value_map.get(member.member_id, 0.0)
        elif member.member_type == TARGET_MEMBER_INSTRUMENT:
            actual_value = position_value_by_instrument.get(member.member_id, 0.0)
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
        execution_status = "ready"
        execution_note = None
        if gap is not None:
            if (
                str(row.get("member_type") or "") == "instrument"
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
                "execution_status": execution_status,
                "execution_note": execution_note,
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
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    market_data: ResearchMarketDataContext | None = None,
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

    taxonomy_scope = str(taxonomy.get("primary_assignment_scope") or "")
    node_rows = [
        item
        for item in list_taxonomy_nodes(portfolio_id)
        if str(item.get("taxonomy_id") or "") == planning_taxonomy_id
        and str(item.get("status") or "") == "active"
        and not (taxonomy_scope == TARGET_MEMBER_INSTRUMENT and _taxonomy_node_row_is_system_cash_like(item))
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
        node_id = str(assignment.get("taxonomy_node_id") or "")
        if node_id not in node_by_id:
            continue
        direct_assignments_by_node[node_id].append(assignment)

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
        base_currency=_normalized_currency(
            portfolio.get("base_currency"),
            context=f"Portfolio '{portfolio_id}' base",
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
        frozen_taxonomy_node_ids=frozenset(
            str(item).strip() for item in (frozen_taxonomy_node_ids or []) if str(item).strip()
        ),
        top_sleeve_weight_bounds=_normalize_top_sleeve_weight_bounds(top_sleeve_weight_bounds),
        market_data=market_data,
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
    start_day = research_window_start_date(as_of_date, lookback_days)
    state = _state_with_locked_market_data(
        state,
        instrument_ids=_research_instrument_ids(
            state,
            scope_node_id=comparator_taxonomy_node_id,
        ),
        start_date=start_day,
        end_date=as_of_date,
    )
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


def _top_sleeve_for_member(
    state: TaxonomyResearchState,
    *,
    member_type: str,
    member_id: str,
) -> tuple[str | None, str, str]:
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
        except ResearchMarketDataError:
            raise
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
                "solved_weight": 0.0,
                "target_risk_share": group_target_risk_by_id.get(top_sleeve_id or ""),
                "forward_risk_contribution": 0.0,
                "min_weight": _safe_float((group_bounds or {}).get("min_weight")),
                "max_weight": _safe_float((group_bounds or {}).get("max_weight")),
                "bound_status": None,
                "rows": [],
            },
        )
        solved_weight = _safe_float(leaf.get("target_weight"))
        forward_rc = forward_rc_by_key.get(_target_key(leaf))
        row = {
            "member_type": member_type,
            "member_id": member_id,
            "label": str(leaf.get("label") or member_id),
            "top_sleeve_id": top_sleeve_id,
            "top_sleeve_label": top_sleeve_label,
            "solved_weight": solved_weight,
            "target_risk_share": _selected_target_risk_share(leaf),
            "forward_risk_contribution": forward_rc,
        }
        group["rows"].append(row)
        group["solved_weight"] = float(group["solved_weight"] or 0.0) + float(solved_weight or 0.0)
        if forward_rc is not None:
            group["forward_risk_contribution"] = float(group["forward_risk_contribution"] or 0.0) + float(forward_rc)

    rendered = list(groups.values())
    for group in rendered:
        rows = list(group.get("rows") or [])
        rows.sort(key=lambda item: abs(_safe_float(item.get("solved_weight")) or 0.0), reverse=True)
        group["rows"] = rows
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
    dates: list[date] = []
    current = first_month
    while current <= end_date:
        dates.append(current)
        current = (pd.Timestamp(current) + pd.DateOffset(months=month_step)).date()
    if not dates and start_date <= end_date:
        return [start_date]
    return dates


def _nav_returns(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="float64")
    cleaned = series.sort_index().astype("float64")
    return cleaned.pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _compound_return(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    for value in values:
        total *= 1.0 + float(value)
    return total - 1.0


def _build_backtest_metrics(points: list[dict[str, object]], returns: dict[str, float]) -> dict[str, object]:
    dates: list[date] = []
    for item in points:
        parsed_date = _parse_iso_date(item.get("date"))
        if parsed_date is not None:
            dates.append(parsed_date)
    raw_history_reliability = build_performance_history_reliability(
        {
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if dates else None,
            "snapshot_count": len(dates),
            "return_observation_count": len(returns),
            "risk_return_observation_count": len(returns),
        }
    )
    history_reliability = {
        **raw_history_reliability,
        "start_date": (
            raw_history_reliability["start_date"].isoformat()
            if isinstance(raw_history_reliability.get("start_date"), date)
            else None
        ),
        "end_date": (
            raw_history_reliability["end_date"].isoformat()
            if isinstance(raw_history_reliability.get("end_date"), date)
            else None
        ),
    }
    if len(points) < 2 or not returns:
        return {
            "method_version": RESEARCH_BACKTEST_METRICS_METHOD_VERSION,
            "history_reliability": history_reliability,
            "start_date": points[0]["date"] if points else None,
            "end_date": points[-1]["date"] if points else None,
            "period_return": None,
            "ytd_return": None,
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
    start_value = _safe_float(points[0].get("value")) or 1.0
    end_value = _safe_float(points[-1].get("value")) or start_value
    return_dates = [_parse_iso_date(date_key) for date_key in returns.keys()]
    periods_per_year = _infer_periods_per_year([item for item in return_dates if item is not None])
    period_count = max(len(returns), 1)
    period_return = end_value / start_value - 1.0 if abs(start_value) > 1e-12 else None
    end_date = dates[-1] if dates else None
    ytd_return_values = [
        float(value)
        for date_key, value in sorted(returns.items())
        if (parsed_date := _parse_iso_date(date_key)) is not None
        and end_date is not None
        and parsed_date.year == end_date.year
    ]
    ytd_return = _compound_return(ytd_return_values)
    geometric_annualized_return = (
        (end_value / start_value) ** (periods_per_year / period_count) - 1.0
        if period_return is not None and end_value > 0 and start_value > 0
        else None
    )
    annualized_return = (
        geometric_annualized_return
        if history_reliability["annualized_return_eligible"]
        else None
    )
    return_values = np.asarray(list(returns.values()), dtype="float64")
    annualized_volatility = (
        float(np.nanstd(return_values, ddof=1) * sqrt(periods_per_year)) if len(return_values) > 1 else None
    )
    arithmetic_annualized_return = (
        float(np.mean(return_values) * periods_per_year)
        if len(return_values) > 0 and np.all(np.isfinite(return_values))
        else None
    )
    sharpe_ratio = (
        float(arithmetic_annualized_return / annualized_volatility)
        if arithmetic_annualized_return is not None
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
        "method_version": RESEARCH_BACKTEST_METRICS_METHOD_VERSION,
        "history_reliability": history_reliability,
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "period_return": period_return,
        "ytd_return": ytd_return,
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
    )


def _instrument_label(state: TaxonomyResearchState, instrument_id: str) -> str:
    if state.market_data is not None:
        return state.market_data.instrument_name_by_id.get(instrument_id, instrument_id)
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


def _resolved_backtest_calculation_frequency(solution: dict[str, object], requested_frequency: object) -> CalculationFrequency:
    profile = solution.get("calculation_frequency")
    if isinstance(profile, dict):
        resolved = str(profile.get("resolved_frequency") or "").strip().lower()
        if resolved in RESEARCH_COVARIANCE_FREQUENCY_PARAMETERS:
            return resolved  # type: ignore[return-value]
    requested = str(requested_frequency or "").strip().lower()
    if requested in RESEARCH_COVARIANCE_FREQUENCY_PARAMETERS:
        return requested  # type: ignore[return-value]
    return "daily"


def _build_backtest_sampled_nav_by_instrument(
    nav_by_instrument: dict[str, pd.Series],
    *,
    calculation_frequency: CalculationFrequency,
    end_date: date,
) -> dict[str, pd.Series]:
    sampled: dict[str, pd.Series] = {}
    for instrument_id, series in nav_by_instrument.items():
        sampled_series = _periodic_nav_series(
            series,
            calculation_frequency=calculation_frequency,
            start_date=date(1900, 1, 1),
            end_date=end_date,
        )
        if not sampled_series.empty:
            sampled[instrument_id] = sampled_series
    return sampled


def _common_return_dates(returns_by_instrument: dict[str, pd.Series]) -> list[date]:
    return_sets = [set(series.index.tolist()) for series in returns_by_instrument.values() if not series.empty]
    if not return_sets:
        return []
    common_dates = set.intersection(*return_sets)
    return sorted(item for item in common_dates if isinstance(item, date))


def _backtest_rebalance_dates(
    *,
    start_date: date,
    end_date: date,
    frequency: str,
    calculation_frequency: CalculationFrequency,
    returns_by_instrument: dict[str, pd.Series],
) -> list[date]:
    if frequency == "1w" and calculation_frequency != "daily":
        return [item for item in _common_return_dates(returns_by_instrument) if start_date <= item <= end_date]
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
    try:
        benchmark_nav, benchmark_warnings = _build_instrument_nav_series(
            state,
            instrument_id=normalized_benchmark_id,
            start_date=date(1900, 1, 1),
            end_date=state.as_of_date,
            warn_on_start_clip=False,
        )
    except ResearchMarketDataError:
        raise
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
    state = _state_with_locked_market_data(
        state,
        instrument_ids=[benchmark_instrument_id],
        start_date=date(1900, 1, 1),
        end_date=as_of_date,
    )
    return _build_backtest_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id=benchmark_instrument_id,
        portfolio_points=portfolio_points,
    )


def _active_backtest_leaf_ids(solution: dict[str, object]) -> list[str]:
    instrument_ids = [
        str(row.get("member_id") or "")
        for row in list(solution.get("leaf_targets") or [])
        if str(row.get("member_type") or "") == TARGET_MEMBER_INSTRUMENT
        and abs(float(_safe_float(row.get("target_weight")) or 0.0)) > 1e-12
    ]
    return list(dict.fromkeys(item for item in instrument_ids if item))


def build_current_target_backtest(
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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    frozen_taxonomy_node_ids: list[str] | None = None,
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    risk_model_config: dict[str, object] | None = None,
    rebalance_frequency: str = "1m",
    benchmark_instrument_id: str | None = None,
    current_solution: dict[str, object] | None = None,
) -> dict[str, object]:
    frequency = _normalize_backtest_rebalance_frequency(rebalance_frequency)
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        frozen_taxonomy_node_ids=frozen_taxonomy_node_ids,
        top_sleeve_weight_bounds=top_sleeve_weight_bounds,
    )
    state = _state_with_locked_market_data(
        state,
        instrument_ids=_research_instrument_ids(
            state,
            scope_node_id=comparator_taxonomy_node_id,
            additional_instrument_ids=[benchmark_instrument_id or ""],
        ),
        start_date=date(1900, 1, 1),
        end_date=as_of_date,
    )
    solution = current_solution or _solve_current_target_weights_from_state(
        state,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
        target_dimension=target_dimension,
        capital_mode=capital_mode,
        gross_exposure=gross_exposure,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
        missing_return_policy=missing_return_policy,
        risk_model_config=risk_model_config,
    )
    active_leaf_ids = _active_backtest_leaf_ids(solution)
    warnings: list[str] = list(RESEARCH_BACKTEST_METHODOLOGY_WARNINGS)
    if not active_leaf_ids:
        empty_backtest = {
            "rebalance_frequency": frequency,
            "common_history_start_date": None,
            "start_date": None,
            "end_date": as_of_date.isoformat(),
            "lookback_days": lookback_days,
            "points": [],
            "metrics": _build_backtest_metrics([], {}),
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": list(
                dict.fromkeys(
                    [
                        *warnings,
                        "Backtest requires at least one solved instrument.",
                    ]
                )
            ),
        }
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
            "market_data_dependencies": _research_market_data_manifest(state),
        }

    nav_by_instrument: dict[str, pd.Series] = {}
    for instrument_id in active_leaf_ids:
        try:
            nav_series, instrument_warnings = _build_instrument_nav_series(
                state,
                instrument_id=instrument_id,
                start_date=date(1900, 1, 1),
                end_date=as_of_date,
                warn_on_start_clip=False,
            )
        except ResearchMarketDataError:
            raise
        except ValueError as error:
            warnings.append(f"{instrument_id} excluded from backtest: {error}")
            continue
        if nav_series.empty:
            continue
        nav_by_instrument[instrument_id] = nav_series
        warnings.extend(instrument_warnings)

    backtest_calculation_frequency = _resolved_backtest_calculation_frequency(solution, calculation_frequency)
    sampled_nav_by_instrument = _build_backtest_sampled_nav_by_instrument(
        nav_by_instrument,
        calculation_frequency=backtest_calculation_frequency,
        end_date=as_of_date,
    )
    portfolio_first_dates = [series.index[0] for series in sampled_nav_by_instrument.values() if not series.empty]

    if not sampled_nav_by_instrument or not portfolio_first_dates:
        empty_backtest = {
            "rebalance_frequency": frequency,
            "common_history_start_date": None,
            "start_date": None,
            "end_date": as_of_date.isoformat(),
            "lookback_days": lookback_days,
            "points": [],
            "metrics": _build_backtest_metrics([], {}),
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": list(dict.fromkeys(warnings or ["Backtest has no usable instrument history."])),
        }
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
            "market_data_dependencies": _research_market_data_manifest(state),
        }

    common_start = max(portfolio_first_dates)
    earliest_start_date = (
        pd.Timestamp(common_start) + pd.DateOffset(months=_research_window_months(lookback_days))
    ).date()
    if earliest_start_date > as_of_date:
        empty_backtest = {
            "rebalance_frequency": frequency,
            "common_history_start_date": common_start.isoformat(),
            "start_date": None,
            "end_date": as_of_date.isoformat(),
            "lookback_days": lookback_days,
            "points": [],
            "metrics": _build_backtest_metrics([], {}),
            "top_sleeve_weight_points": [],
            "top_sleeve_contribution_points": [],
            "warnings": list(
                dict.fromkeys(
                    [
                        *warnings,
                        "Backtest requires portfolio member common history at least as long as the selected risk window.",
                    ]
                )
            ),
        }
        return {
            "backtest": empty_backtest,
            "backtest_benchmark": None,
            "backtest_relative_metrics": None,
            "market_data_dependencies": _research_market_data_manifest(state),
        }
    returns_by_instrument = {instrument_id: _nav_returns(nav) for instrument_id, nav in sampled_nav_by_instrument.items()}
    rebal_dates = _backtest_rebalance_dates(
        start_date=earliest_start_date,
        end_date=as_of_date,
        frequency=frequency,
        calculation_frequency=backtest_calculation_frequency,
        returns_by_instrument=returns_by_instrument,
    )
    if not rebal_dates:
        rebal_dates = [earliest_start_date]

    portfolio_nav = 1.0
    points: list[dict[str, object]] = []
    portfolio_returns: dict[str, float] = {}
    contribution_accumulator: dict[str, float] = defaultdict(float)
    weight_points: list[dict[str, object]] = []
    contribution_points: list[dict[str, object]] = []
    last_period_weights: dict[str, float] | None = None
    last_top_by_instrument: dict[str, tuple[str | None, str]] = {}

    def sleeve_weight_point(
        date_key: str,
        weights_by_instrument: dict[str, float],
        top_lookup: dict[str, tuple[str | None, str]],
    ) -> dict[str, object]:
        top_weight_by_key: dict[str, dict[str, object]] = {}
        for instrument_id, weight in weights_by_instrument.items():
            if abs(weight) <= 1e-12:
                continue
            top_id, top_label = top_lookup.get(instrument_id, (None, "Unassigned"))
            top_key = top_id or "__unassigned__"
            sleeve = top_weight_by_key.setdefault(
                top_key,
                {"top_sleeve_id": top_id, "top_sleeve_label": top_label, "value": 0.0},
            )
            sleeve["value"] = float(sleeve["value"] or 0.0) + float(weight)
        return {
            "date": date_key,
            "sleeves": sorted(
                top_weight_by_key.values(),
                key=lambda item: abs(_safe_float(item.get("value")) or 0.0),
                reverse=True,
            ),
        }

    for index, rebalance_date in enumerate(rebal_dates):
        period_end = rebal_dates[index + 1] if index + 1 < len(rebal_dates) else as_of_date
        try:
            period_solution = _solve_current_target_weights_from_state(
                replace(
                    state,
                    as_of_date=rebalance_date,
                    current_valuation_cache={},
                ),
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
                risk_model_config=risk_model_config,
                include_actuals=False,
            )
        except ResearchMarketDataError:
            raise
        except ValueError as error:
            if not _is_rebalance_data_gap_error(error):
                raise ValueError(f"{rebalance_date.isoformat()} rebalance failed: {error}") from error
            if not points:
                warnings.append(
                    f"{rebalance_date.isoformat()} rebalance skipped during backtest warm-up: {error}"
                )
                continue
            if last_period_weights is None:
                raise ValueError(f"{rebalance_date.isoformat()} rebalance failed: {error}") from error
            warnings.append(
                f"{rebalance_date.isoformat()} rebalance skipped; previous weights carried forward: {error}"
            )
            period_weights = dict(last_period_weights)
            top_by_instrument = dict(last_top_by_instrument)
            weight_points.append(
                sleeve_weight_point(rebalance_date.isoformat(), period_weights, top_by_instrument)
            )
        else:
            if not points:
                points.append({"date": rebalance_date.isoformat(), "value": portfolio_nav})

            leaf_weights = {
                str(row.get("member_id") or ""): _safe_float(row.get("target_weight")) or 0.0
                for row in list(period_solution.get("leaf_targets") or [])
                if str(row.get("member_type") or "") == TARGET_MEMBER_INSTRUMENT
            }
            top_by_instrument = {}
            for row in list(period_solution.get("leaf_targets") or []):
                if str(row.get("member_type") or "") != TARGET_MEMBER_INSTRUMENT:
                    continue
                instrument_id = str(row.get("member_id") or "")
                top_id, top_label, _path = _top_sleeve_for_member(
                    state,
                    member_type=TARGET_MEMBER_INSTRUMENT,
                    member_id=instrument_id,
                )
                top_by_instrument[instrument_id] = (top_id, top_label)
            period_weights = {
                instrument_id: float(weight)
                for instrument_id, weight in leaf_weights.items()
                if instrument_id in returns_by_instrument
            }
            weight_points.append(
                sleeve_weight_point(rebalance_date.isoformat(), period_weights, top_by_instrument)
            )

        active_instruments = [
            instrument_id
            for instrument_id, weight in period_weights.items()
            if abs(weight) > 1e-12 and instrument_id in returns_by_instrument
        ]
        candidate_dates = sorted(
            {
                return_date
                for instrument_id in active_instruments
                for return_date in returns_by_instrument.get(instrument_id, pd.Series(dtype="float64")).index
                if rebalance_date < return_date <= period_end
            }
        )
        for return_date in candidate_dates:
            if any(return_date not in returns_by_instrument[instrument_id].index for instrument_id in active_instruments):
                continue
            sleeve_contribution: dict[str, dict[str, object]] = {}
            portfolio_return = 0.0
            for instrument_id in active_instruments:
                instrument_return = _safe_float(returns_by_instrument[instrument_id].get(return_date))
                if instrument_return is None:
                    portfolio_return = np.nan
                    break
                weighted_return = float(period_weights[instrument_id]) * instrument_return
                portfolio_return += weighted_return
                top_id, top_label = top_by_instrument.get(instrument_id, (None, "Unassigned"))
                top_key = top_id or "__unassigned__"
                sleeve = sleeve_contribution.setdefault(
                    top_key,
                    {"top_sleeve_id": top_id, "top_sleeve_label": top_label, "value": 0.0},
                )
                sleeve["value"] = float(sleeve["value"] or 0.0) + weighted_return
            if not np.isfinite(portfolio_return):
                continue
            date_key = return_date.isoformat()
            portfolio_returns[date_key] = float(portfolio_return)
            period_growth = 1.0 + float(portfolio_return)
            if period_growth <= 0.0:
                raise ValueError(f"{return_date.isoformat()} backtest portfolio NAV became non-positive.")
            for instrument_id in active_instruments:
                instrument_return = _safe_float(returns_by_instrument[instrument_id].get(return_date)) or 0.0
                period_weights[instrument_id] = (
                    float(period_weights[instrument_id]) * (1.0 + instrument_return) / period_growth
                )
            portfolio_nav *= period_growth
            points.append({"date": date_key, "value": portfolio_nav})
            weight_points.append(sleeve_weight_point(date_key, period_weights, top_by_instrument))
            sleeves_for_date = []
            for top_key, sleeve in sleeve_contribution.items():
                contribution_accumulator[top_key] += float(sleeve.get("value") or 0.0)
                sleeves_for_date.append(
                    {
                        "top_sleeve_id": sleeve.get("top_sleeve_id"),
                        "top_sleeve_label": sleeve.get("top_sleeve_label"),
                        "value": contribution_accumulator[top_key],
                    }
                )
            contribution_points.append({"date": date_key, "sleeves": sleeves_for_date})
        last_period_weights = dict(period_weights)
        last_top_by_instrument = dict(top_by_instrument)

    comparison_payload = _build_backtest_benchmark_comparison_from_state(
        state,
        benchmark_instrument_id=benchmark_instrument_id,
        portfolio_points=points,
        portfolio_returns=portfolio_returns,
    )

    backtest = {
        "rebalance_frequency": frequency,
        "common_history_start_date": common_start.isoformat(),
        "start_date": points[0]["date"] if points else None,
        "end_date": points[-1]["date"] if points else as_of_date.isoformat(),
        "lookback_days": lookback_days,
        "points": points,
        "metrics": _build_backtest_metrics(points, portfolio_returns),
        "top_sleeve_weight_points": weight_points,
        "top_sleeve_contribution_points": contribution_points,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return {
        "backtest": backtest,
        "backtest_benchmark": comparison_payload.get("backtest_benchmark"),
        "backtest_relative_metrics": comparison_payload.get("backtest_relative_metrics"),
        "market_data_dependencies": _research_market_data_manifest(state),
    }


def _solve_current_target_weights_from_state(
    state: TaxonomyResearchState,
    *,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date,
    lookback_days: int,
    calculation_frequency: str = "auto",
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    risk_model_config: dict[str, object] | None = None,
    include_actuals: bool = True,
) -> dict[str, object]:
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    start_day = research_window_start_date(as_of_date, lookback_days)
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
        missing_return_policy=_normalize_missing_return_policy(missing_return_policy),
        apply_capital_overlay=comparator_taxonomy_node_id is None,
        risk_model_config=risk_model_config,
        include_actuals=include_actuals,
    )
    if include_actuals:
        actual_rows, actual_warnings = _current_scope_actuals(
            state,
            scope_node_id=comparator_taxonomy_node_id,
            as_of_date=as_of_date,
        )
        warnings = list(dict.fromkeys([*scope_result.warnings, *actual_warnings]))
        solved_result_groups, solved_result_warnings = _build_solved_result_groups(
            state,
            leaf_rows=scope_result.leaf_target_rows,
            member_rows=scope_result.member_target_rows,
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
        )
    else:
        actual_rows = []
        warnings = list(dict.fromkeys(scope_result.warnings))
        solved_result_groups = []
        target_weight_gaps = []
    return {
        "portfolio_id": state.portfolio_id,
        "planning_taxonomy_id": state.planning_taxonomy_id,
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
        "solved_result_groups": solved_result_groups,
        "actual_rows": deepcopy(actual_rows),
        "resolved_target_rows": deepcopy(scope_result.resolved_target_rows),
        "solve_event": deepcopy(scope_result.solve_event),
        "scope_solve_events": deepcopy(scope_result.scope_solve_events),
        "target_weight_gaps": target_weight_gaps,
        "warnings": warnings,
        "return_observations": float(len(scope_result.return_series)),
        "calculation_frequency": frequency_profile,
        "market_data_dependencies": _research_market_data_manifest(state),
    }


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
    missing_return_policy: str = RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    frozen_taxonomy_node_ids: list[str] | None = None,
    top_sleeve_weight_bounds: list[dict[str, object]] | None = None,
    risk_model_config: dict[str, object] | None = None,
    include_actuals: bool = True,
) -> dict[str, object]:
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
        frozen_taxonomy_node_ids=frozen_taxonomy_node_ids,
        top_sleeve_weight_bounds=top_sleeve_weight_bounds,
    )
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    start_day = research_window_start_date(as_of_date, lookback_days)
    state = _state_with_locked_market_data(
        state,
        instrument_ids=_research_instrument_ids(
            state,
            scope_node_id=comparator_taxonomy_node_id,
        ),
        start_date=start_day,
        end_date=as_of_date,
    )
    return _solve_current_target_weights_from_state(
        state,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        calculation_frequency=calculation_frequency,
        target_dimension=target_dimension,
        capital_mode=capital_mode,
        gross_exposure=gross_exposure,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
        missing_return_policy=missing_return_policy,
        risk_model_config=risk_model_config,
        include_actuals=include_actuals,
    )
