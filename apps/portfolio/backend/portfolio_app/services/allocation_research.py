from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from portfolio_app.services.allocation_solver import (
    AllocationResearchState,
    ROOT_SCOPE_LABEL,
    ROOT_SCOPE_MEMBER_ID,
    TARGET_DIMENSION_RISK_BUDGET,
    TARGET_DIMENSION_WEIGHT,
    TARGET_MEMBER_INSTRUMENT,
    TARGET_MEMBER_NODE,
    ScopeMemberRecord,
    _current_scope_actuals,
    _normalize_top_sleeve_weight_bounds,
    _safe_float,
    _solver_return_window,
    _scope_source_frequencies,
    _solve_current_scope,
    _taxonomy_node_row_is_system_cash_like,
)
from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
)
from portfolio_app.services.portfolio_market_data import (
    PortfolioMarketDataContext,
    PortfolioMarketDataError,
    _build_instrument_nav_series,
    _lock_portfolio_market_data,
    _normalized_currency,
    portfolio_market_data_manifest,
)
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_accounts,
    list_target_set_lines,
    list_target_sets,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
)
from portfolio_app.services.risk_math import (
    DEFAULT_MISSING_RETURN_POLICY,
    _estimate_covariance,
    _normalize_missing_return_policy,
    _risk_contribution_shares,
    _risk_model_contribution_mode,
    _risk_model_covariance_model_id,
    _risk_model_covariance_parameters,
    risk_window_start_date,
)


def _allocation_instrument_ids(
    state: AllocationResearchState,
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


def _state_with_locked_portfolio_market_data(
    state: AllocationResearchState,
    *,
    instrument_ids: list[str],
    start_date: date,
    end_date: date,
) -> AllocationResearchState:
    context = _lock_portfolio_market_data(
        instrument_ids=instrument_ids,
        base_currency=state.base_currency,
        start_date=start_date,
        end_date=end_date,
    )
    return replace(state, market_data=context)


def _portfolio_market_data_manifest_from_state(
    state: AllocationResearchState,
) -> dict[str, object] | None:
    context = state.market_data
    if context is None:
        return None
    return portfolio_market_data_manifest(context)


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
                    "Allocation Research does not infer an executable liquidation from target eligibility or limited history."
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
    market_data: PortfolioMarketDataContext | None = None,
) -> AllocationResearchState:
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

    return AllocationResearchState(
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


def build_allocation_research_scope_options(
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


def build_allocation_research_calculation_frequency_profile(
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
        raise ValueError("Selected allocation research scope was not found in the planning taxonomy.")
    start_day = risk_window_start_date(as_of_date, lookback_days)
    state = _state_with_locked_portfolio_market_data(
        state,
        instrument_ids=_allocation_instrument_ids(
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
    state: AllocationResearchState,
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
    state: AllocationResearchState,
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

    start_day = risk_window_start_date(as_of_date, lookback_days)
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
        except PortfolioMarketDataError:
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
    state: AllocationResearchState,
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


def _solve_current_target_weights_from_state(
    state: AllocationResearchState,
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
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
    risk_model_config: dict[str, object] | None = None,
    include_actuals: bool = True,
) -> dict[str, object]:
    if comparator_taxonomy_node_id and comparator_taxonomy_node_id not in state.node_by_id:
        raise ValueError("Selected allocation research scope was not found in the planning taxonomy.")
    start_day = risk_window_start_date(as_of_date, lookback_days)
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
        "market_data_dependencies": _portfolio_market_data_manifest_from_state(state),
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
    missing_return_policy: str = DEFAULT_MISSING_RETURN_POLICY,
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
        raise ValueError("Selected allocation research scope was not found in the planning taxonomy.")
    start_day = risk_window_start_date(as_of_date, lookback_days)
    state = _state_with_locked_portfolio_market_data(
        state,
        instrument_ids=_allocation_instrument_ids(
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
