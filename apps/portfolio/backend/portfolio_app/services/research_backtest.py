from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from math import isfinite, sqrt

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import portfolio_app.services.performance as performance_service
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.performance import build_statement_of_assets_report
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
TARGET_MODE_SAA = "saa"
TARGET_MODE_TAA_OVER_SAA = "taa_over_saa"
TARGET_DIMENSION_SCOPE_DEFAULT = "scope_default"
TARGET_DIMENSION_WEIGHT = "weight"
TARGET_DIMENSION_RISK_BUDGET = "risk_budget"
REBALANCE_FREQUENCIES = {"weekly", "monthly", "quarterly"}
TARGET_MEMBER_NODE = "taxonomy_node"
TARGET_MEMBER_INSTRUMENT = "instrument"
TARGET_MEMBER_CASH = "cash_bucket"


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
class ScopeSimulationResult:
    scope_node_id: str | None
    scope_label: str
    scope_path: str
    default_target_dimension: str
    scope_depth: int
    member_source: str
    nav_frame: pd.DataFrame
    weight_frame: pd.DataFrame
    member_summary_rows: list[dict[str, object]]
    rebalance_events: list[dict[str, object]]
    warnings: list[str]
    latest_target_rows: list[dict[str, object]]


@dataclass(frozen=True)
class TaxonomyBacktestState:
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
    direct_fx_assets: dict[tuple[str, str], str]


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


def _build_direct_fx_asset_map() -> dict[tuple[str, str], str]:
    direct_assets: dict[tuple[str, str], str] = {}
    payload = performance_service.get_platform_fx_rates()
    if not isinstance(payload, dict):
        return direct_assets
    for item in payload.get("rates", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("source_kind") or "") != "direct":
            continue
        base_currency = _normalized_currency(item.get("base_currency"), fallback="")
        quote_currency = _normalized_currency(item.get("quote_currency"), fallback="")
        asset_id = str(item.get("asset_id") or "").strip()
        if base_currency and quote_currency and asset_id:
            direct_assets[(base_currency, quote_currency)] = asset_id
    return direct_assets


def _instrument_detail(
    state: TaxonomyBacktestState,
    asset_id: str,
) -> dict[str, object] | None:
    if asset_id not in state.instrument_detail_cache:
        state.instrument_detail_cache[asset_id] = performance_service.get_registry_instrument_detail(asset_id)
    return state.instrument_detail_cache[asset_id]


def _candidate_quote_bases(detail: dict[str, object]) -> list[str]:
    policy = detail.get("quote_selection_policy", {})
    candidate_bases: list[str] = []
    if isinstance(policy, dict):
        # Research/backtest should consume total-return series whenever the shared
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
        if not isinstance(raw_point, dict):
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

    fallback_points = sorted(
        (item for values in points_by_basis.values() for item in values),
        key=lambda item: item[0],
    )
    return fallback_points


def _convert_price_to_base(
    state: TaxonomyBacktestState,
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
        direct_assets=state.direct_fx_assets,
        instrument_detail_cache=state.instrument_detail_cache,
    )
    rate = _safe_float((fx or {}).get("rate"))
    if rate is None or rate <= 0:
        return None
    return float(value) * rate


def _build_instrument_nav_series(
    state: TaxonomyBacktestState,
    *,
    asset_id: str,
    start_date: date,
    end_date: date,
) -> tuple[pd.Series, list[str]]:
    detail = _instrument_detail(state, asset_id)
    if not isinstance(detail, dict):
        raise ValueError(f"Instrument detail for {asset_id} is unavailable.")

    selected_points = _selected_price_points(detail, end_date=end_date)
    warnings: list[str] = []
    if not selected_points:
        raise ValueError(f"{asset_id} does not have usable market history for the requested period.")

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
        raise ValueError(f"{asset_id} does not have FX-complete market history for the requested period.")

    series = pd.Series({point_date: base_value for point_date, base_value in rows}, dtype="float64").sort_index()
    visible = series.loc[(series.index >= start_date) & (series.index <= end_date)]
    if visible.empty:
        visible = series.loc[series.index <= end_date]
    if visible.empty:
        raise ValueError(f"{asset_id} does not have any observations on or before the selected end date.")
    if visible.index[0] > start_date:
        warnings.append(
            f"{asset_id} history starts on {visible.index[0].isoformat()}, so the backtest window is clipped for this member."
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


def _infer_periods_per_year(dates: list[date]) -> float:
    if len(dates) < 2:
        return 1.0
    timestamps = pd.to_datetime(pd.Series(list(dates))).sort_values()
    day_gaps = timestamps.diff().dropna().dt.days
    if day_gaps.empty:
        return 1.0
    median_gap = max(float(day_gaps.median()), 1.0)
    if median_gap <= 2.0:
        return 252.0
    if median_gap <= 10.0:
        return 52.0
    if median_gap <= 45.0:
        return 12.0
    if median_gap <= 120.0:
        return 4.0
    if median_gap <= 240.0:
        return 2.0
    if median_gap <= 400.0:
        return 1.0
    return 365.0 / median_gap


def _compute_performance_metrics(nav_frame: pd.DataFrame) -> dict[str, float]:
    if nav_frame.empty:
        return {}
    returns = nav_frame["portfolio_return"].astype("float64").reset_index(drop=True)
    nav = nav_frame["nav"].astype("float64").reset_index(drop=True)
    dates = [_parse_iso_date(value) for value in nav_frame["asof_date"]]
    resolved_dates = [item for item in dates if item is not None]
    effective_returns = returns.iloc[1:].reset_index(drop=True) if len(returns) > 1 else pd.Series(dtype="float64")
    periods_per_year = _infer_periods_per_year(resolved_dates)
    periods = max(len(nav) - 1, 1)
    cumulative_return = float(nav.iloc[-1] - 1.0)
    annualized_return = float(nav.iloc[-1] ** (periods_per_year / periods) - 1.0) if len(nav) > 1 else 0.0
    annualized_volatility = (
        float(effective_returns.std(ddof=0) * sqrt(periods_per_year))
        if not effective_returns.empty
        else 0.0
    )
    max_drawdown = float(nav_frame["drawdown"].min()) if "drawdown" in nav_frame else 0.0
    average_period_return = float(effective_returns.mean() * periods_per_year) if not effective_returns.empty else 0.0
    downside_returns = effective_returns.where(effective_returns < 0.0, 0.0)
    downside_deviation = (
        float(sqrt(float((downside_returns.pow(2).mean() or 0.0))) * sqrt(periods_per_year))
        if not effective_returns.empty
        else 0.0
    )
    sharpe_ratio = average_period_return / annualized_volatility if annualized_volatility > 1e-12 else 0.0
    sortino_ratio = average_period_return / downside_deviation if downside_deviation > 1e-12 else 0.0
    calmar_ratio = annualized_return / abs(max_drawdown) if abs(max_drawdown) > 1e-12 else 0.0
    return {
        "cumulative_return": cumulative_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": float(sharpe_ratio),
        "sortino_ratio": float(sortino_ratio),
        "max_drawdown": max_drawdown,
        "calmar_ratio": float(calmar_ratio),
        "observations": float(len(nav_frame)),
    }


def _risk_share_vector(covariance: np.ndarray, weights: np.ndarray) -> np.ndarray:
    variance = float(weights @ covariance @ weights)
    if variance <= 1e-12:
        return np.full(len(weights), 1.0 / max(len(weights), 1), dtype="float64")
    marginal = covariance @ weights
    contribution = weights * marginal
    total = float(contribution.sum())
    if abs(total) <= 1e-12:
        return np.full(len(weights), 1.0 / max(len(weights), 1), dtype="float64")
    shares = contribution / total
    shares = np.where(np.isfinite(shares), shares, 0.0)
    total_shares = float(shares.sum())
    if abs(total_shares) <= 1e-12:
        return np.full(len(weights), 1.0 / max(len(weights), 1), dtype="float64")
    return shares / total_shares


def _solve_risk_budget_weights(
    *,
    target_shares: np.ndarray,
    return_window: pd.DataFrame,
    reference_weights: np.ndarray | None,
) -> tuple[np.ndarray, float, str]:
    count = len(target_shares)
    if count == 1:
        return np.asarray([1.0], dtype="float64"), 0.0, "single-member"

    if return_window.shape[0] < 2:
        fallback = np.asarray(target_shares, dtype="float64")
        fallback_total = float(fallback.sum())
        if fallback_total <= 1e-12:
            fallback = np.full(count, 1.0 / count, dtype="float64")
        else:
            fallback = fallback / fallback_total
        return fallback, 0.0, "fallback-insufficient-history"

    covariance = return_window.cov(ddof=0).to_numpy(dtype="float64")
    if covariance.shape != (count, count):
        raise ValueError("Risk covariance dimension does not match selected scope members.")
    covariance = np.where(np.isfinite(covariance), covariance, 0.0)
    covariance = covariance + np.eye(count, dtype="float64") * 1e-8

    reference = (
        np.asarray(reference_weights, dtype="float64")
        if reference_weights is not None and len(reference_weights) == count
        else np.asarray(target_shares, dtype="float64")
    )
    reference = np.clip(reference, 0.0, None)
    if float(reference.sum()) <= 1e-12:
        reference = np.full(count, 1.0 / count, dtype="float64")
    else:
        reference = reference / float(reference.sum())

    target = np.asarray(target_shares, dtype="float64")
    target = np.clip(target, 0.0, None)
    if float(target.sum()) <= 1e-12:
        target = np.full(count, 1.0 / count, dtype="float64")
    else:
        target = target / float(target.sum())

    def objective(weights: np.ndarray) -> float:
        shares = _risk_share_vector(covariance, weights)
        share_gap = shares - target
        reference_gap = weights - reference
        return 1_000.0 * float(share_gap @ share_gap) + 0.25 * float(reference_gap @ reference_gap)

    bounds = [(0.0, 1.0)] * count
    constraints = [{"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)}]
    result = minimize(
        objective,
        x0=reference,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 500, "disp": False},
    )
    if not result.success:
        fallback = target
        return fallback, float(np.max(np.abs(_risk_share_vector(covariance, fallback) - target))), "fallback-solver"

    solved = np.asarray(result.x, dtype="float64")
    solved = np.clip(solved, 0.0, None)
    total = float(solved.sum())
    if total <= 1e-12:
        solved = target
    else:
        solved = solved / total
    gap = float(np.max(np.abs(_risk_share_vector(covariance, solved) - target)))
    return solved, gap, "risk-budget"


def _select_rebalance_dates(calendar: list[date], frequency: str) -> list[date]:
    if not calendar:
        return []
    if frequency not in REBALANCE_FREQUENCIES:
        raise ValueError("Unsupported rebalance frequency.")
    date_series = pd.Series(calendar, dtype="object")
    timestamps = pd.to_datetime(date_series)
    if frequency == "weekly":
        grouped = date_series.groupby(timestamps.dt.to_period("W-FRI")).max()
    elif frequency == "quarterly":
        grouped = date_series.groupby(timestamps.dt.to_period("Q")).max()
    else:
        grouped = date_series.groupby(timestamps.dt.to_period("M")).max()
    ordered = [calendar[0]]
    for item in grouped.tolist():
        if item not in ordered:
            ordered.append(item)
    return ordered


def _aligned_calendar(series_list: list[pd.Series], *, start_date: date, end_date: date) -> tuple[list[date], date]:
    if not series_list:
        raise ValueError("Selected scope does not contain any backtest members.")
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
        raise ValueError("Research backtest requires at least two aligned observations.")
    return calendar, effective_start


def _align_member_series(
    members: list[ScopeMemberRecord],
    nav_series_by_member: dict[tuple[str, str], pd.Series],
    *,
    start_date: date,
    end_date: date,
) -> tuple[list[MemberSeries], list[date], list[str]]:
    calendar, effective_start = _aligned_calendar(
        list(nav_series_by_member.values()),
        start_date=start_date,
        end_date=end_date,
    )
    rendered: list[MemberSeries] = []
    warnings: list[str] = []
    for member in members:
        raw_series = nav_series_by_member[(member.member_type, member.member_id)].sort_index()
        visible = raw_series.loc[raw_series.index <= end_date]
        aligned = visible.reindex(calendar, method="ffill")
        if aligned.isna().any():
            raise ValueError(f"{member.label} does not have enough history for aligned backtest dates.")
        base_value = float(aligned.iloc[0])
        if abs(base_value) <= 1e-12:
            raise ValueError(f"{member.label} starts with a non-positive base value.")
        normalized_nav = aligned / base_value
        returns = normalized_nav.pct_change().fillna(0.0)
        cumulative_return = float(normalized_nav.iloc[-1] - 1.0)
        if raw_series.index[0] > start_date:
            warnings.append(
                f"{member.label} history begins on {raw_series.index[0].isoformat()}, clipping selected backtest start to {effective_start.isoformat()}."
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
    state: TaxonomyBacktestState,
    *,
    scope_node_id: str | None,
    as_of_date: date,
    target_set_type: str,
) -> dict[str, object] | None:
    candidates = state.target_sets_by_scope_type.get((scope_node_id, target_set_type), [])
    active = [
        item
        for item in candidates
        if _period_active(
            effective_from=item.get("effective_from"),
            effective_to=item.get("effective_to"),
            as_of_date=as_of_date,
        )
    ]
    if not active:
        return None
    active.sort(
        key=lambda item: (
            str(item.get("effective_from") or ""),
            str(item.get("target_set_id") or ""),
        ),
        reverse=True,
    )
    return active[0]


def _resolve_dimension_target_rows(
    state: TaxonomyBacktestState,
    *,
    scope_node_id: str | None,
    scope_members: list[ScopeMemberRecord],
    as_of_date: date,
    target_set_mode: str,
    selected_dimension: str,
) -> tuple[list[dict[str, object]], list[str]]:
    warnings: list[str] = []
    if target_set_mode not in {TARGET_MODE_SAA, TARGET_MODE_TAA_OVER_SAA}:
        raise ValueError("Unsupported target set mode.")

    preferred_dimensions = [selected_dimension]
    if selected_dimension == TARGET_DIMENSION_SCOPE_DEFAULT:
        default_dimension = _scope_default_target_dimension(state, scope_node_id)
        preferred_dimensions = [default_dimension]
        alternate_dimension = (
            TARGET_DIMENSION_RISK_BUDGET
            if default_dimension == TARGET_DIMENSION_WEIGHT
            else TARGET_DIMENSION_WEIGHT
        )
        preferred_dimensions.append(alternate_dimension)

    candidate_types = ["saa"] if target_set_mode == TARGET_MODE_SAA else ["taa", "saa"]
    line_keys = [(member.member_type, member.member_id) for member in scope_members]
    fallback_notice: str | None = None

    for dimension_index, resolved_dimension in enumerate(preferred_dimensions):
        enabled_field = "weight_enabled" if resolved_dimension == TARGET_DIMENSION_WEIGHT else "risk_budget_enabled"
        value_field = "target_weight" if resolved_dimension == TARGET_DIMENSION_WEIGHT else "target_risk_share"
        for target_set_type in candidate_types:
            target_set = _scope_target_sets(
                state,
                scope_node_id=scope_node_id,
                as_of_date=as_of_date,
                target_set_type=target_set_type,
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
                if fallback_notice:
                    warnings.append(fallback_notice)
                return rendered_rows, warnings
        if selected_dimension == TARGET_DIMENSION_SCOPE_DEFAULT and dimension_index == 0:
            fallback_notice = (
                f"Scope default target for {state.node_by_id.get(scope_node_id, {}).get('node_name') or ROOT_SCOPE_LABEL} "
                f"could not be resolved as {preferred_dimensions[0]}; falling back to {preferred_dimensions[1]}."
            )

    if scope_members:
        resolved_dimension = preferred_dimensions[0]
        equal_share = 1.0 / float(len(scope_members))
        scope_label = str(state.node_by_id.get(scope_node_id, {}).get("node_name") or ROOT_SCOPE_LABEL)
        warnings.append(
            f"{scope_label} does not have an active {resolved_dimension} target set, so Research is using an equal-{resolved_dimension.replace('_', '-')} local default."
        )
        return [
            {
                "member_type": member.member_type,
                "member_id": member.member_id,
                "label": member.label,
                "taxonomy_node_id": member.taxonomy_node_id,
                "default_target_dimension": member.default_target_dimension,
                "selected_dimension": resolved_dimension,
                "selected_value": equal_share,
                "target_weight": equal_share if resolved_dimension == TARGET_DIMENSION_WEIGHT else None,
                "target_risk_share": equal_share if resolved_dimension == TARGET_DIMENSION_RISK_BUDGET else None,
                "source_target_set_id": None,
                "source_target_set_type": None,
            }
            for member in scope_members
        ], warnings

    raise ValueError("No active target set can resolve the requested scope and target dimension.")


def _scope_members(
    state: TaxonomyBacktestState,
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
            label = str((detail or {}).get("asset_name") or target_entity_id)
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


def _scope_default_target_dimension(state: TaxonomyBacktestState, scope_node_id: str | None) -> str:
    if scope_node_id:
        return str(state.node_by_id.get(scope_node_id, {}).get("default_target_dimension") or TARGET_DIMENSION_WEIGHT)
    return str(state.root_default_target_dimension or TARGET_DIMENSION_WEIGHT)


def _simulation_window(return_series: pd.Series, *, as_of_date: date, lookback_days: int) -> pd.Series:
    if return_series.empty:
        return return_series
    start_date = as_of_date - pd.Timedelta(days=max(int(lookback_days), 1))
    start_day = start_date.date() if hasattr(start_date, "date") else start_date
    window = return_series.loc[(return_series.index >= start_day) & (return_series.index <= as_of_date)]
    return window.astype("float64")


def _simulate_scope(
    state: TaxonomyBacktestState,
    *,
    scope_node_id: str | None,
    start_date: date,
    end_date: date,
    lookback_days: int,
    target_set_mode: str,
    target_dimension: str,
    rebalance_frequency: str,
) -> ScopeSimulationResult:
    scope_label = str(state.node_by_id.get(scope_node_id, {}).get("node_name") or ROOT_SCOPE_LABEL)
    scope_path = state.node_path_by_id.get(scope_node_id or ROOT_SCOPE_MEMBER_ID, ROOT_SCOPE_LABEL)
    scope_depth = int(state.node_depth_by_id.get(scope_node_id, 0))
    default_target_dimension = _scope_default_target_dimension(state, scope_node_id)

    members, member_source = _scope_members(state, scope_node_id=scope_node_id)

    nav_series_by_member: dict[tuple[str, str], pd.Series] = {}
    child_warnings: list[str] = []
    member_results_cache: dict[str, ScopeSimulationResult] = {}
    member_cumulative_returns: dict[tuple[str, str], float] = {}

    for member in members:
        if member.member_type == TARGET_MEMBER_NODE:
            child_result = _simulate_scope(
                state,
                scope_node_id=member.member_id,
                start_date=start_date,
                end_date=end_date,
                lookback_days=lookback_days,
                target_set_mode=target_set_mode,
                target_dimension=target_dimension,
                rebalance_frequency=rebalance_frequency,
            )
            member_results_cache[member.member_id] = child_result
            member_nav = pd.Series(
                child_result.nav_frame["nav"].astype("float64").tolist(),
                index=pd.Index(
                    [_parse_iso_date(item) for item in child_result.nav_frame["asof_date"]],
                    dtype="object",
                ),
                dtype="float64",
            )
            nav_series_by_member[(member.member_type, member.member_id)] = member_nav
            member_cumulative_returns[(member.member_type, member.member_id)] = float(member_nav.iloc[-1] - 1.0)
            child_warnings.extend(child_result.warnings)
        elif member.member_type == TARGET_MEMBER_CASH:
            cash_nav = _build_cash_nav_series(start_date=start_date, end_date=end_date)
            nav_series_by_member[(member.member_type, member.member_id)] = cash_nav
            member_cumulative_returns[(member.member_type, member.member_id)] = float(cash_nav.iloc[-1] - 1.0)
        else:
            instrument_nav, instrument_warnings = _build_instrument_nav_series(
                state,
                asset_id=member.member_id,
                start_date=start_date,
                end_date=end_date,
            )
            nav_series_by_member[(member.member_type, member.member_id)] = instrument_nav
            member_cumulative_returns[(member.member_type, member.member_id)] = float(
                instrument_nav.iloc[-1] / instrument_nav.iloc[0] - 1.0
            )
            child_warnings.extend(instrument_warnings)

    aligned_members, calendar, alignment_warnings = _align_member_series(
        members,
        nav_series_by_member,
        start_date=start_date,
        end_date=end_date,
    )
    child_warnings.extend(alignment_warnings)

    returns_frame = pd.DataFrame(
        {
            member.member.label: member.returns
            for member in aligned_members
        }
    )
    returns_frame.columns = [f"{member.member.member_type}::{member.member.member_id}" for member in aligned_members]
    label_by_key = {
        f"{member.member.member_type}::{member.member.member_id}": member.member.label
        for member in aligned_members
    }
    member_by_key = {
        f"{member.member.member_type}::{member.member.member_id}": member.member
        for member in aligned_members
    }
    rebalance_dates = _select_rebalance_dates(calendar, rebalance_frequency)

    current_weights = pd.Series(dtype="float64")
    residual_cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    nav_rows: list[dict[str, object]] = []
    weight_rows: list[dict[str, object]] = []
    latest_target_rows: list[dict[str, object]] = []
    warnings: list[str] = []
    rebalance_events: list[dict[str, object]] = []

    for index, as_of_date in enumerate(calendar):
        rebalance_candidate = as_of_date in rebalance_dates
        realized_returns = returns_frame.loc[as_of_date]
        portfolio_return = 0.0
        rebalance_flag = False
        turnover = None
        target_dimension_used = None
        pre_rebalance_weights = current_weights.copy()

        if index == 0:
            rebalance_flag = True
            rebalance_candidate = True
        else:
            if not current_weights.empty:
                portfolio_return = float((current_weights * realized_returns.reindex(current_weights.index, fill_value=0.0)).sum())
            daily_nav_factor = 1.0 + portfolio_return
            nav *= daily_nav_factor
            if nav <= 0:
                raise ValueError(f"{scope_label} backtest NAV became non-positive.")
            if not current_weights.empty:
                grown = current_weights * (1.0 + realized_returns.reindex(current_weights.index, fill_value=0.0))
                current_weights = grown / daily_nav_factor
            residual_cash_weight = residual_cash_weight / daily_nav_factor

        if rebalance_candidate:
            resolved_rows, resolution_warnings = _resolve_dimension_target_rows(
                state,
                scope_node_id=scope_node_id,
                scope_members=members,
                as_of_date=as_of_date,
                target_set_mode=target_set_mode,
                selected_dimension=target_dimension,
            )
            warnings.extend(resolution_warnings)
            target_dimension_used = str(resolved_rows[0]["selected_dimension"]) if resolved_rows else TARGET_DIMENSION_WEIGHT
            target_values = pd.Series(
                {
                    f"{row['member_type']}::{row['member_id']}": float(row["selected_value"])
                    for row in resolved_rows
                },
                dtype="float64",
            )
            target_values = target_values.reindex(returns_frame.columns, fill_value=0.0)

            if target_dimension_used == TARGET_DIMENSION_RISK_BUDGET:
                return_window = pd.DataFrame(
                    {
                        column: _simulation_window(
                            returns_frame[column],
                            as_of_date=as_of_date,
                            lookback_days=lookback_days,
                        )
                        for column in returns_frame.columns
                    }
                ).dropna(how="all")
                reference = (
                    current_weights.reindex(returns_frame.columns, fill_value=0.0).to_numpy(dtype="float64")
                    if not current_weights.empty
                    else None
                )
                solved_weights, risk_gap, solver_kind = _solve_risk_budget_weights(
                    target_shares=target_values.to_numpy(dtype="float64"),
                    return_window=return_window.fillna(0.0),
                    reference_weights=reference,
                )
                implementation_weights = pd.Series(solved_weights, index=returns_frame.columns, dtype="float64")
                if solver_kind.startswith("fallback"):
                    warnings.append(
                        f"{scope_label} used {solver_kind} on {as_of_date.isoformat()} because local covariance was weak."
                    )
            else:
                implementation_weights = target_values
                risk_gap = None
                solver_kind = "weight"

            for row in resolved_rows:
                member_key = f"{row['member_type']}::{row['member_id']}"
                row["implementation_weight"] = float(implementation_weights.get(member_key, 0.0))

            turnover = (
                float(0.5 * np.abs(implementation_weights - current_weights.reindex(implementation_weights.index, fill_value=0.0)).sum())
                if not current_weights.empty
                else float(0.5 * np.abs(implementation_weights).sum())
            )
            pre_total = float(pre_rebalance_weights.sum()) if not pre_rebalance_weights.empty else 0.0
            pre_gap = (
                float(np.max(np.abs(pre_rebalance_weights.reindex(implementation_weights.index, fill_value=0.0) - implementation_weights)))
                if not implementation_weights.empty
                else 0.0
            )
            current_weights = implementation_weights
            residual_cash_weight = 1.0 - float(current_weights.sum())
            rebalance_flag = True
            latest_target_rows = deepcopy(resolved_rows)
            rebalance_events.append(
                {
                    "rebalance_date": as_of_date.isoformat(),
                    "scope_label": scope_label,
                    "target_dimension": target_dimension_used,
                    "solver_kind": solver_kind,
                    "turnover": turnover,
                    "pre_rebalance_weight_total": pre_total,
                    "target_weight_total": float(current_weights.sum()),
                    "max_weight_gap_before_rebalance": pre_gap,
                    "max_risk_share_gap": risk_gap,
                    "member_count": len(members),
                }
            )

        peak_nav = max(peak_nav, nav)
        drawdown = nav / peak_nav - 1.0
        nav_rows.append(
            {
                "asof_date": as_of_date.isoformat(),
                "portfolio_return": portfolio_return,
                "nav": nav,
                "drawdown": drawdown,
                "rebalance_flag": rebalance_flag,
            }
        )
        for column in returns_frame.columns:
            member = member_by_key[column]
            weight_rows.append(
                {
                    "asof_date": as_of_date.isoformat(),
                    "scope_label": scope_label,
                    "member_type": member.member_type,
                    "member_id": member.member_id,
                    "label": member.label,
                    "weight": float(current_weights.reindex(returns_frame.columns, fill_value=0.0)[column]),
                    "rebalance_flag": rebalance_flag,
                    "target_dimension": target_dimension_used,
                }
            )
        weight_rows.append(
            {
                "asof_date": as_of_date.isoformat(),
                "scope_label": scope_label,
                "member_type": "residual_cash",
                "member_id": "residual_cash",
                "label": "Residual Cash",
                "weight": residual_cash_weight,
                "rebalance_flag": rebalance_flag,
                "target_dimension": target_dimension_used,
            }
        )

    nav_frame = pd.DataFrame(nav_rows)
    weight_frame = pd.DataFrame(weight_rows)
    summary_rows: list[dict[str, object]] = []
    for column in returns_frame.columns:
        member = member_by_key[column]
        member_weight_history = weight_frame.loc[
            (weight_frame["member_type"] == member.member_type)
            & (weight_frame["member_id"] == member.member_id)
        ]
        latest_target = next(
            (
                row
                for row in latest_target_rows
                if row["member_type"] == member.member_type and row["member_id"] == member.member_id
            ),
            None,
        )
        summary_rows.append(
            {
                "member_type": member.member_type,
                "member_id": member.member_id,
                "label": member.label,
                "default_target_dimension": member.default_target_dimension,
                "selected_target_dimension": latest_target.get("selected_dimension") if latest_target else None,
                "source_target_set_type": latest_target.get("source_target_set_type") if latest_target else None,
                "start_weight": _safe_float(member_weight_history["weight"].iloc[0]) if not member_weight_history.empty else None,
                "end_weight": _safe_float(member_weight_history["weight"].iloc[-1]) if not member_weight_history.empty else None,
                "weight_change": (
                    float(member_weight_history["weight"].iloc[-1] - member_weight_history["weight"].iloc[0])
                    if len(member_weight_history) >= 2
                    else None
                ),
                "average_weight": float(member_weight_history["weight"].mean()) if not member_weight_history.empty else None,
                "min_weight": float(member_weight_history["weight"].min()) if not member_weight_history.empty else None,
                "max_weight": float(member_weight_history["weight"].max()) if not member_weight_history.empty else None,
                "latest_target_weight": _safe_float(latest_target.get("target_weight")) if latest_target else None,
                "latest_target_risk_share": _safe_float(latest_target.get("target_risk_share")) if latest_target else None,
                "latest_implementation_weight": _safe_float(latest_target.get("implementation_weight")) if latest_target else None,
                "selected_target_value": _safe_float(latest_target.get("selected_value")) if latest_target else None,
                "cumulative_return": member_cumulative_returns.get((member.member_type, member.member_id)),
            }
        )
    warnings.extend(child_warnings)
    deduped_warnings = list(dict.fromkeys(item for item in warnings if item))
    return ScopeSimulationResult(
        scope_node_id=scope_node_id,
        scope_label=scope_label,
        scope_path=scope_path,
        default_target_dimension=default_target_dimension,
        scope_depth=scope_depth,
        member_source=member_source,
        nav_frame=nav_frame,
        weight_frame=weight_frame,
        member_summary_rows=summary_rows,
        rebalance_events=rebalance_events,
        warnings=deduped_warnings,
        latest_target_rows=latest_target_rows,
    )


def _current_scope_actuals(
    state: TaxonomyBacktestState,
    *,
    scope_node_id: str | None,
    as_of_date: date,
) -> tuple[list[dict[str, object]], list[str]]:
    portfolio = get_portfolio(state.portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")
    accounts = list_accounts(state.portfolio_id)
    transactions = list_transactions(state.portfolio_id)
    statement = build_statement_of_assets_report(
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

    position_value_by_asset: dict[str, float] = {}
    for position in list(statement.get("positions") or []):
        asset_id = str(position.get("asset_id") or "")
        if asset_id:
            position_value_by_asset[asset_id] = float(_safe_float(position.get("market_value_base")) or 0.0)

    visible_cash_accounts = [
        account_row
        for account_row in list(account_workspace.get("accounts") or [])
        if str((account_row.get("account") or {}).get("account_type") or "") == "deposit_account"
    ]
    cash_value_by_account = {
        str((account_row.get("account") or {}).get("account_id") or ""): float(
            _safe_float(account_row.get("derived_cash_balance_base")) or 0.0
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

    for asset_id, market_value_base in position_value_by_asset.items():
        node_id = direct_position_membership.get(asset_id)
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
            actual_value = position_value_by_asset.get(member.member_id, 0.0)
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


def _build_rebalance_suggestions(
    *,
    actual_rows: list[dict[str, object]],
    latest_target_rows: list[dict[str, object]],
    base_currency: str,
) -> list[dict[str, object]]:
    target_by_member = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in latest_target_rows
    }
    suggestions: list[dict[str, object]] = []
    for actual_row in actual_rows:
        key = (str(actual_row.get("member_type") or ""), str(actual_row.get("member_id") or ""))
        target_row = target_by_member.get(key)
        target_weight = _safe_float((target_row or {}).get("implementation_weight"))
        current_weight = _safe_float(actual_row.get("current_weight"))
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
        suggestions.append(
            {
                "member_type": actual_row.get("member_type"),
                "member_id": actual_row.get("member_id"),
                "label": actual_row.get("label"),
                "current_weight": current_weight,
                "target_weight": target_weight,
                "gap": gap,
                "current_value_base": _safe_float(actual_row.get("current_value_base")),
                "base_currency": base_currency,
                "action": action,
            }
        )
    for target_row in latest_target_rows:
        key = (str(target_row.get("member_type") or ""), str(target_row.get("member_id") or ""))
        if any(
            str(item.get("member_type") or "") == key[0] and str(item.get("member_id") or "") == key[1]
            for item in actual_rows
        ):
            continue
        suggestions.append(
            {
                "member_type": target_row.get("member_type"),
                "member_id": target_row.get("member_id"),
                "label": target_row.get("label"),
                "current_weight": 0.0,
                "target_weight": _safe_float(target_row.get("implementation_weight")),
                "gap": _safe_float(target_row.get("implementation_weight")),
                "current_value_base": 0.0,
                "base_currency": base_currency,
                "action": "Add",
            }
        )
    suggestions.sort(key=lambda item: abs(_safe_float(item.get("gap")) or 0.0), reverse=True)
    return suggestions


def _build_taxonomy_state(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    as_of_date: date,
) -> TaxonomyBacktestState:
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

    return TaxonomyBacktestState(
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
        direct_fx_assets=_build_direct_fx_asset_map(),
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


def run_taxonomy_backtest(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str,
    comparator_taxonomy_node_id: str | None,
    start_date: date,
    end_date: date,
    lookback_days: int,
    target_set_mode: str,
    target_dimension: str,
    rebalance_frequency: str,
) -> dict[str, object]:
    state = _build_taxonomy_state(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=end_date,
    )
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected research scope was not found in the planning taxonomy.")
    if end_date < start_date:
        raise ValueError("Backtest end date must not be earlier than start date.")

    scope_result = _simulate_scope(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        start_date=start_date,
        end_date=end_date,
        lookback_days=lookback_days,
        target_set_mode=target_set_mode,
        target_dimension=target_dimension,
        rebalance_frequency=rebalance_frequency,
    )
    actual_rows, actual_warnings = _current_scope_actuals(
        state,
        scope_node_id=comparator_taxonomy_node_id,
        as_of_date=end_date,
    )
    warnings = list(dict.fromkeys([*scope_result.warnings, *actual_warnings]))
    suggestions = _build_rebalance_suggestions(
        actual_rows=actual_rows,
        latest_target_rows=scope_result.latest_target_rows,
        base_currency=state.base_currency,
    )
    metrics = _compute_performance_metrics(scope_result.nav_frame)
    curve_points = [
        {
            "date": str(row["asof_date"]),
            "nav": float(row["nav"]),
            "drawdown": float(row["drawdown"]),
            "portfolio_return": float(row["portfolio_return"]),
            "rebalance_flag": bool(row["rebalance_flag"]),
        }
        for row in scope_result.nav_frame.to_dict(orient="records")
    ]
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
        "metrics": metrics,
        "curve_points": curve_points,
        "member_summaries": deepcopy(scope_result.member_summary_rows),
        "actual_rows": deepcopy(actual_rows),
        "latest_target_rows": deepcopy(scope_result.latest_target_rows),
        "rebalance_events": deepcopy(scope_result.rebalance_events),
        "rebalance_suggestions": suggestions,
        "warnings": warnings,
        "weight_schedule_rows": scope_result.weight_frame.to_dict(orient="records"),
    }
