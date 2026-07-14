from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.services.portfolio_market_data import (
    PortfolioMarketDataContext,
    PortfolioMarketDataError,
    _build_instrument_nav_series,
    _resolved_total_return_points,
)
from portfolio_app.services.published_holdings import read_current_published_holdings
from portfolio_app.services.risk_math import (
    DEFAULT_RISK_CONTRIBUTION_MODE,
    MISSING_RETURN_POLICY_STRICT,
    _aligned_calendar,
    _annualized_portfolio_volatility,
    _clean_return_frame,
    _estimate_covariance,
    _normalize_missing_return_policy,
    _periodic_series_by_member,
    _prepare_return_window_for_covariance,
    _risk_contribution_shares,
    _risk_model_contribution_mode,
    _risk_model_covariance_model_id,
    _risk_model_covariance_parameters,
    _series_observation_frequency,
    risk_window_start_date,
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


ALLOCATION_MAX_RISK_BUDGET_SHARE_GAP = 1e-4


RISK_BUDGET_NORMALIZED_GAP_FLOOR_EQUAL_SHARE_FRACTION = 0.25


RISK_BUDGET_NORMALIZED_GAP_MAX_FLOOR = 0.05


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
    contribution_mode: str = DEFAULT_RISK_CONTRIBUTION_MODE


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
class AllocationResearchState:
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
    market_data: "PortfolioMarketDataContext | None" = field(
        default=None,
        repr=False,
        compare=False,
    )
    # A current-target solve walks the taxonomy recursively.  Current holdings
    # and account values are portfolio-level inputs, so rebuilding both ledgers
    # once per scope is redundant and can make deep taxonomies disproportionately
    # expensive.  Keep the lazy valuation snapshot on the per-solve state; a new
    # state is constructed for every as-of date (including each Policy Replay
    # rebalance), so this cache never crosses a point-in-time boundary.
    current_valuation_cache: dict[str, object] = field(default_factory=dict, repr=False, compare=False)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _build_cash_nav_series(
    *,
    start_date: date,
    end_date: date,
) -> pd.Series:
    calendar = pd.date_range(start=start_date, end=end_date, freq="D").date
    if len(calendar) == 0:
        calendar = [start_date]
    return pd.Series(1.0, index=pd.Index(calendar, dtype="object"), dtype="float64")


def _node_is_cash_subtree(state: AllocationResearchState, node_id: str) -> bool:
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


def _member_is_cash_like(state: AllocationResearchState, member: ScopeMemberRecord) -> bool:
    if member.member_type == TARGET_MEMBER_CASH:
        return True
    if member.member_type != TARGET_MEMBER_NODE:
        return False
    return _node_is_cash_subtree(state, member.member_id)


def _taxonomy_node_row_is_system_cash_like(node: dict[str, object]) -> bool:
    normalized_name = str(node.get("node_name") or "").strip().lower()
    normalized_code = str(node.get("node_code") or "").strip().lower()
    return normalized_code == "cash" or normalized_name in {"cash", "现金"}


def _scope_is_frozen(state: AllocationResearchState, scope_node_id: str | None) -> bool:
    if scope_node_id is None:
        return False
    return scope_node_id in state.frozen_taxonomy_node_ids


def _member_is_frozen(
    state: AllocationResearchState,
    *,
    scope_node_id: str | None,
    member: ScopeMemberRecord,
) -> bool:
    if _member_is_cash_like(state, member):
        return False
    if _scope_is_frozen(state, scope_node_id):
        return True
    return member.member_type == TARGET_MEMBER_NODE and member.member_id in state.frozen_taxonomy_node_ids


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


def _normalize_positive_vector(values: np.ndarray) -> np.ndarray:
    vector = np.clip(np.asarray(values, dtype="float64"), 0.0, None)
    total = float(vector.sum())
    if total <= 1e-12:
        raise ValueError("Target vector must contain at least one positive value.")
    return vector / total


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
        if primary.max_abs_share_gap <= ALLOCATION_MAX_RISK_BUDGET_SHARE_GAP:
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
    if enforce_tolerance and best.max_abs_share_gap > ALLOCATION_MAX_RISK_BUDGET_SHARE_GAP + 1e-12:
        raise ValueError(
            "Risk budget solver could not satisfy target risk shares within "
            f"{ALLOCATION_MAX_RISK_BUDGET_SHARE_GAP:.2%}; achieved max gap {best.max_abs_share_gap:.2%}."
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
            raise ValueError(f"{member.label} does not have enough history for aligned allocation research dates.")
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
                f"{member.label} history begins on {raw_series.index[0].isoformat()}, clipping selected allocation research start to {effective_start.isoformat()}."
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
    state: AllocationResearchState,
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
    state: AllocationResearchState,
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
    state: AllocationResearchState,
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
    state: AllocationResearchState,
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
            except PortfolioMarketDataError:
                continue
            dates = [
                point.observation_date
                for point in points
            ]
            frequencies.append(_series_observation_frequency(pd.Series(1.0, index=pd.Index(dates, dtype="object"))))
    return frequencies


def _scope_default_target_dimension(state: AllocationResearchState, scope_node_id: str | None) -> str:
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
    start_day = risk_window_start_date(as_of_date, lookback_days)
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
    state: AllocationResearchState,
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
    state: AllocationResearchState,
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
    start_day = risk_window_start_date(as_of_date, lookback_days)

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
                f"{scope_label} had no positive risky target weight before capital overlay, so Allocation Research used equal risky-sleeve weights."
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
            contribution_mode=risk_solve.risk_contribution_mode or DEFAULT_RISK_CONTRIBUTION_MODE,
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
    state: AllocationResearchState,
    *,
    scope_node_id: str | None,
    as_of_date: date,
) -> tuple[list[dict[str, object]], list[str]]:
    cache_key = f"actual-valuation::{as_of_date.isoformat()}"
    cached_valuation = state.current_valuation_cache.get(cache_key)
    if isinstance(cached_valuation, dict):
        position_value_by_instrument = dict(cached_valuation.get("position_value_by_instrument") or {})
        cash_value_by_account = dict(cached_valuation.get("cash_value_by_account") or {})
        pending_settlement_value = float(
            cached_valuation["pending_settlement_value"]
        )
    else:
        statement = read_current_published_holdings(
            state.portfolio_id,
            as_of_date=as_of_date,
        )
        if statement.base_currency != state.base_currency:
            raise ValueError(
                "Published current allocation currency does not match the allocation research state."
            )

        position_value_by_instrument: dict[str, float] = {}
        for position in statement.positions:
            market_value_base = _safe_float(position.market_value_base_exact)
            if market_value_base is None:
                raise ValueError(
                    "Current published allocation valuation is incomplete; "
                    "price and FX coverage must be complete before solving."
                )
            position_value_by_instrument[position.instrument_id] = market_value_base

        if any(
            _safe_float(account.value_base_exact) is None
            for account in statement.cash_accounts
        ):
            raise ValueError(
                "Current published cash valuation is incomplete; FX coverage "
                "must be complete before solving."
            )
        cash_value_by_account = {
            account.account_id: float(account.value_base_exact)
            for account in statement.cash_accounts
            if account.value_base_exact is not None
        }
        pending_settlement_value = _safe_float(
            statement.pending_settlement_base_exact
        )
        if pending_settlement_value is None:
            raise ValueError(
                "Current published pending-settlement valuation is incomplete."
            )
        state.current_valuation_cache[cache_key] = {
            "position_value_by_instrument": dict(position_value_by_instrument),
            "cash_value_by_account": dict(cash_value_by_account),
            "pending_settlement_value": pending_settlement_value,
        }

    cash_total_value = float(
        sum(cash_value_by_account.values()) + pending_settlement_value
    )
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
            "Allocation Research target solve requires complete planning-taxonomy coverage; "
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
