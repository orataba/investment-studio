from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
from math import isfinite, sqrt
from typing import cast

import numpy as np
import pandas as pd

from portfolio_app.core.operating_profiles import require_portfolio_operating_profile
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.calculation_frequency import (
    CalculationFrequency,
    calculation_frequency_profile,
    default_calculation_frequency,
    infer_observation_frequency,
)
from portfolio_app.services.fact_currency import require_portfolio_fact_currency
from portfolio_app.services.holding_identity import is_cash_holding_instrument_id
from portfolio_app.services.published_holdings import (
    PublishedCashAccountValue,
    PublishedHoldingsStatement,
    PublishedHoldingsUnavailableError,
    read_current_published_holdings,
)
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_portfolio_instrument_universe,
    list_target_set_lines,
    list_target_sets,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
)
from portfolio_app.services.portfolio_market_data import (
    PortfolioMarketDataError,
    build_canonical_total_return_nav_series,
    lock_portfolio_market_data_in_session,
    portfolio_market_data_manifest,
)
from portfolio_app.services.risk_math import (
    align_nav_series_to_calculation_frequency,
    prepare_return_window_for_covariance,
)
from portfolio_app.services.risk_model import (
    estimate_risk_statistics,
    normalize_portfolio_risk_policy,
    portfolio_risk_model_snapshot,
    risk_policy_covariance_parameters,
    weighted_risk_contribution,
)


RISK_WORKSPACE_ENGINE_VERSION = "portfolio-risk-workspace-v1"
RISK_HISTORY_START_DATE = date(1900, 1, 1)
SUPPORTED_RISK_WORKSPACE_LOOKBACK_DAYS = frozenset({30, 90, 180, 366, 730})
ALL_INSTRUMENTS_SCOPE = "__all_instruments__"
TOP_LEVEL_TAXONOMY_SCOPE = "__taxonomy_top_level__"
MAX_ROLLING_OUTPUT_POINTS = 320
MAX_MATRIX_AS_OF_OPTIONS = 320
_EPSILON = 1e-12


class RiskWorkspaceNotFoundError(LookupError):
    pass


class RiskWorkspaceRequestError(ValueError):
    pass


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _normalized_lookback(value: object, *, field_name: str) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise RiskWorkspaceRequestError(f"{field_name} must be a supported risk window.") from error
    if resolved not in SUPPORTED_RISK_WORKSPACE_LOOKBACK_DAYS:
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_RISK_WORKSPACE_LOOKBACK_DAYS))
        raise RiskWorkspaceRequestError(f"{field_name} must be one of: {supported} days.")
    return resolved


def _error_payload(
    message: str,
    *,
    reason_codes: list[str] | tuple[str, ...] | None = None,
    dependency: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "message": str(message),
        "reason_codes": list(dict.fromkeys(reason_codes or ["calculation_unavailable"])),
    }
    if dependency is not None:
        payload["dependency"] = deepcopy(dependency)
    return payload


def _exception_error(error: Exception) -> dict[str, object]:
    if isinstance(error, PortfolioMarketDataError):
        return _error_payload(
            str(error),
            reason_codes=list(error.reason_codes),
            dependency=error.dependency,
        )
    return _error_payload(str(error))


def _unavailable_section(*errors: dict[str, object], **values: object) -> dict[str, object]:
    return {"status": "unavailable", "errors": list(errors), **values}


def _ready_section(**values: object) -> dict[str, object]:
    return {"status": "ready", "errors": [], **values}


def _not_applicable_section(**values: object) -> dict[str, object]:
    return {"status": "not_applicable", "errors": [], **values}


def _current_holding_records(
    statement: PublishedHoldingsStatement,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for position in statement.positions:
        market_value = _safe_float(position.market_value_base_exact)
        weight = _safe_float(position.portfolio_weight)
        has_exposure = (
            position.quantity_exact != 0
            or abs(market_value or 0.0) > 1e-9
            or abs(weight or 0.0) > 1e-9
        )
        if not has_exposure:
            continue
        rows.append(
            {
                "instrument_id": position.instrument_id,
                "label": position.instrument_name,
                "market_value_base": market_value,
                "weight": weight,
            }
        )
    return sorted(rows, key=lambda item: (-abs(_safe_float(item.get("weight")) or 0.0), str(item["label"])))


def _universe_records(
    records: list[dict[str, object]],
    holdings: list[dict[str, object]],
) -> list[dict[str, object]]:
    label_by_id = {str(item["instrument_id"]): str(item["label"]) for item in holdings}
    result: dict[str, dict[str, object]] = {}
    for record in records:
        if str(record.get("status") or "active") != "active":
            continue
        instrument_id = str(record.get("instrument_id") or "").strip()
        ref = record.get("instrument_ref") if isinstance(record.get("instrument_ref"), dict) else {}
        instrument_type = str((ref or {}).get("instrument_type") or "").strip().lower()
        if not instrument_id or instrument_type == "cash" or is_cash_holding_instrument_id(instrument_id):
            continue
        result[instrument_id] = {
            "instrument_id": instrument_id,
            "label": str((ref or {}).get("instrument_name") or label_by_id.get(instrument_id) or instrument_id),
        }
    for holding in holdings:
        instrument_id = str(holding["instrument_id"])
        result.setdefault(instrument_id, {"instrument_id": instrument_id, "label": str(holding["label"])})
    return sorted(result.values(), key=lambda item: str(item["label"]))


def _returns_from_nav(
    nav: pd.Series,
    *,
    calculation_frequency: CalculationFrequency,
    as_of_date: date,
) -> pd.Series:
    if nav.empty:
        return pd.Series(dtype="float64")
    numeric = pd.to_numeric(nav, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if bool((numeric.dropna() <= 0.0).any()):
        raise ValueError("Canonical total-return levels must be positive.")
    periodic = align_nav_series_to_calculation_frequency(
        numeric,
        calculation_frequency=calculation_frequency,
        start_date=RISK_HISTORY_START_DATE,
        end_date=as_of_date,
    )
    return periodic.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).iloc[1:]


def _return_frame(
    return_series_by_id: dict[str, pd.Series],
    instrument_ids: list[str],
) -> pd.DataFrame:
    if not instrument_ids:
        return pd.DataFrame()
    return pd.concat(
        {instrument_id: return_series_by_id[instrument_id] for instrument_id in instrument_ids},
        axis=1,
    ).sort_index()


def _portfolio_return_series(
    frame: pd.DataFrame,
    weights: dict[str, float],
) -> pd.Series:
    ordered = [column for column in frame.columns if column in weights]
    if not ordered:
        return pd.Series(dtype="float64")
    vector = pd.Series({column: weights[column] for column in ordered}, dtype="float64")
    return frame[ordered].mul(vector, axis=1).sum(axis=1, min_count=len(ordered)).rename("portfolio")


def _policy_with_lookback(policy: dict[str, object], lookback_days: int) -> dict[str, object]:
    updated = deepcopy(policy)
    updated["lookback_days"] = lookback_days
    return updated


def _rolling_points(
    returns: pd.Series,
    *,
    as_of_date: date,
    calculation_frequency: CalculationFrequency,
    risk_policy: dict[str, object],
    label: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    volatility_points: list[dict[str, object]] = []
    sharpe_points: list[dict[str, object]] = []
    if returns.empty:
        return volatility_points, sharpe_points
    frame = returns.to_frame(name=label)
    calculation_dates = list(frame.index)
    if len(calculation_dates) > MAX_ROLLING_OUTPUT_POINTS:
        selected_indexes = sorted(
            {
                int(round(value))
                for value in np.linspace(
                    0,
                    len(calculation_dates) - 1,
                    MAX_ROLLING_OUTPUT_POINTS,
                )
            }
        )
        calculation_dates = [calculation_dates[index] for index in selected_indexes]
    for raw_date in calculation_dates:
        point_date = pd.Timestamp(raw_date).date()
        if point_date > as_of_date:
            continue
        try:
            result = estimate_risk_statistics(
                frame.loc[:raw_date],
                as_of_date=point_date,
                calculation_frequency=calculation_frequency,
                risk_policy=risk_policy,
                label=f"Rolling risk for {label}",
            )
        except ValueError:
            continue
        volatility = _safe_float(result["annualized_volatility"].get(label))
        sharpe = _safe_float(result["sharpe_ratio"].get(label))
        if volatility is not None:
            volatility_points.append({"date": point_date.isoformat(), "value": volatility})
        if sharpe is not None:
            sharpe_points.append({"date": point_date.isoformat(), "value": sharpe})
    return volatility_points, sharpe_points


def _node_path(
    node_id: str,
    nodes_by_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    current_id: str | None = node_id
    seen: set[str] = set()
    while current_id:
        if current_id in seen:
            raise ValueError(f"Taxonomy hierarchy contains a cycle at {current_id}.")
        seen.add(current_id)
        current = nodes_by_id.get(current_id)
        if current is None:
            raise ValueError(f"Taxonomy assignment references missing active node {current_id}.")
        result.insert(0, current)
        current_id = str(current.get("parent_taxonomy_node_id") or "").strip() or None
    return result


def _assignment_lookup(
    assignments: list[dict[str, object]],
    *,
    taxonomy_id: str,
) -> dict[tuple[str, str], dict[str, object]]:
    result: dict[tuple[str, str], dict[str, object]] = {}
    for assignment in assignments:
        if (
            str(assignment.get("taxonomy_id") or "") != taxonomy_id
            or str(assignment.get("status") or "active") != "active"
        ):
            continue
        key = (
            str(assignment.get("target_scope") or ""),
            str(assignment.get("target_entity_id") or ""),
        )
        if key in result:
            raise ValueError(
                f"Multiple active taxonomy assignments exist for {key[0]} {key[1]}."
            )
        result[key] = assignment
    return result


def _scoped_group(
    *,
    target_scope: str,
    target_entity_id: str,
    scope_node_id: str | None,
    assignments: dict[tuple[str, str], dict[str, object]],
    nodes_by_id: dict[str, dict[str, object]],
) -> tuple[str, str]:
    assignment = assignments.get((target_scope, target_entity_id))
    if assignment is None:
        raise ValueError(f"Taxonomy assignment is required for {target_scope} {target_entity_id}.")
    path = _node_path(str(assignment.get("taxonomy_node_id") or ""), nodes_by_id)
    if not path:
        raise ValueError(f"Taxonomy path is unavailable for {target_scope} {target_entity_id}.")
    if not scope_node_id:
        selected = path[0]
    else:
        scope_index = next(
            (index for index, node in enumerate(path) if str(node.get("taxonomy_node_id")) == scope_node_id),
            None,
        )
        if scope_index is None:
            raise KeyError(target_entity_id)
        selected = path[scope_index + 1] if scope_index + 1 < len(path) else path[scope_index]
    return str(selected["taxonomy_node_id"]), str(selected.get("node_name") or selected["taxonomy_node_id"])


def _taxonomy_group_frame(
    *,
    instrument_frame: pd.DataFrame,
    holdings: list[dict[str, object]],
    scope_node_id: str | None,
    assignments: dict[tuple[str, str], dict[str, object]],
    nodes_by_id: dict[str, dict[str, object]],
) -> tuple[pd.DataFrame, list[dict[str, object]], list[dict[str, object]]]:
    members_by_group: dict[str, list[tuple[str, float]]] = defaultdict(list)
    labels: dict[str, str] = {}
    errors: list[dict[str, object]] = []
    for holding in holdings:
        instrument_id = str(holding["instrument_id"])
        weight = _safe_float(holding.get("weight"))
        if weight is None:
            errors.append(_error_payload(f"Current portfolio weight is unavailable for {holding['label']}."))
            continue
        try:
            group_id, group_label = _scoped_group(
                target_scope="instrument",
                target_entity_id=instrument_id,
                scope_node_id=scope_node_id,
                assignments=assignments,
                nodes_by_id=nodes_by_id,
            )
        except KeyError:
            continue
        except ValueError as error:
            errors.append(_error_payload(str(error), reason_codes=["taxonomy_assignment_unavailable"]))
            continue
        members_by_group[group_id].append((instrument_id, weight))
        labels[group_id] = group_label
    if errors:
        return pd.DataFrame(), [], errors

    grouped: dict[str, pd.Series] = {}
    metadata: list[dict[str, object]] = []
    for group_id, members in members_by_group.items():
        group_weight = sum(weight for _instrument_id, weight in members)
        if abs(group_weight) <= _EPSILON:
            errors.append(
                _error_payload(
                    f"Taxonomy group {labels[group_id]} has offsetting weights and no finite net group weight.",
                    reason_codes=["invalid_group_weight"],
                )
            )
            continue
        member_ids = [instrument_id for instrument_id, _weight in members]
        missing_ids = [instrument_id for instrument_id in member_ids if instrument_id not in instrument_frame.columns]
        if missing_ids:
            errors.append(
                _error_payload(
                    f"Taxonomy group {labels[group_id]} is missing canonical returns for: {', '.join(missing_ids)}.",
                    reason_codes=["incomplete_total_return_coverage"],
                )
            )
            continue
        member_weights = pd.Series(
            {instrument_id: weight / group_weight for instrument_id, weight in members},
            dtype="float64",
        )
        grouped[group_id] = instrument_frame[member_ids].mul(member_weights, axis=1).sum(
            axis=1,
            min_count=len(member_ids),
        )
        metadata.append(
            {
                "key": group_id,
                "label": labels[group_id],
                "weight": group_weight,
            }
        )
    if errors:
        return pd.DataFrame(), [], errors
    return (
        pd.DataFrame(grouped).sort_index(),
        sorted(
            metadata,
            key=lambda item: (-abs(float(item["weight"])), str(item["label"])),
        ),
        [],
    )


def _matrix_scope_options(
    nodes_by_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    options = [{"value": ALL_INSTRUMENTS_SCOPE, "label": "All Instruments", "kind": "instrument"}]
    options.append({"value": TOP_LEVEL_TAXONOMY_SCOPE, "label": "Top Level", "kind": "taxonomy"})
    rendered: list[tuple[str, str]] = []
    for node_id in nodes_by_id:
        path = _node_path(node_id, nodes_by_id)
        rendered.append((node_id, " / ".join(str(node.get("node_name") or node_id) for node in path)))
    options.extend(
        {"value": node_id, "label": label, "kind": "taxonomy"}
        for node_id, label in sorted(rendered, key=lambda item: item[1])
    )
    return options


def _calculable_matrix_dates(
    frame: pd.DataFrame,
    *,
    calculation_frequency: CalculationFrequency,
    risk_policy: dict[str, object],
    required_as_of_date: date | None = None,
) -> list[date]:
    if frame.empty:
        return []
    parameters = risk_policy_covariance_parameters(
        calculation_frequency,
        int(risk_policy["lookback_days"]),
    )
    candidate_dates = list(frame.dropna(how="any").index)
    if len(candidate_dates) > MAX_MATRIX_AS_OF_OPTIONS:
        required_index = next(
            (
                index
                for index, raw_date in enumerate(candidate_dates)
                if pd.Timestamp(raw_date).date() == required_as_of_date
            ),
            None,
        )
        sample_count = (
            MAX_MATRIX_AS_OF_OPTIONS - 1
            if required_index is not None
            else MAX_MATRIX_AS_OF_OPTIONS
        )
        selected_indexes = sorted(
            {
                int(round(value))
                for value in np.linspace(
                    0,
                    len(candidate_dates) - 1,
                    sample_count,
                )
            }
        )
        if required_index is not None:
            selected_indexes = sorted({*selected_indexes, required_index})
        candidate_dates = [candidate_dates[index] for index in selected_indexes]
    result: list[date] = []
    for raw_date in candidate_dates:
        point_date = pd.Timestamp(raw_date).date()
        try:
            prepare_return_window_for_covariance(
                frame.loc[:raw_date],
                lookback_days=int(risk_policy["lookback_days"]),
                min_observations=int(parameters.get("min_observations", 2)),
                label="Risk correlation matrix",
                missing_return_policy=str(risk_policy["missing_return_policy"]),
                calculation_frequency=calculation_frequency,
                as_of_date=point_date,
            )
        except ValueError:
            continue
        result.append(point_date)
    return result


def _build_matrix_section(
    *,
    frame: pd.DataFrame,
    metadata: list[dict[str, object]],
    requested_as_of_date: date | None,
    calculation_frequency: CalculationFrequency,
    risk_policy: dict[str, object],
    scope: str,
) -> dict[str, object]:
    if frame.empty or not metadata:
        return _unavailable_section(
            _error_payload("Correlation matrix requires at least one complete return series."),
            scope=scope,
            as_of_date=None,
            available_as_of_dates=[],
            groups=[],
            cells=[],
            max_abs=0.0,
        )
    dates = _calculable_matrix_dates(
        frame,
        calculation_frequency=calculation_frequency,
        risk_policy=risk_policy,
        required_as_of_date=requested_as_of_date,
    )
    selected_date = requested_as_of_date or (dates[-1] if dates else None)
    if selected_date is None or selected_date not in dates:
        return _unavailable_section(
            _error_payload(
                "Requested matrix as-of date does not have a complete calculable return window.",
                reason_codes=["matrix_as_of_unavailable"],
            ),
            scope=scope,
            as_of_date=selected_date.isoformat() if selected_date else None,
            available_as_of_dates=[item.isoformat() for item in dates],
            groups=[],
            cells=[],
            max_abs=0.0,
        )
    try:
        result = estimate_risk_statistics(
            frame.loc[:selected_date],
            as_of_date=selected_date,
            calculation_frequency=calculation_frequency,
            risk_policy=risk_policy,
            label="Risk correlation matrix",
        )
    except ValueError as error:
        return _unavailable_section(
            _exception_error(error),
            scope=scope,
            as_of_date=selected_date.isoformat(),
            available_as_of_dates=[item.isoformat() for item in dates],
            groups=[],
            cells=[],
            max_abs=0.0,
        )
    correlation: pd.DataFrame = result["correlation"]
    coverage = result["coverage"]
    metadata_by_key = {str(item["key"]): item for item in metadata}
    ordered_keys = [str(column) for column in correlation.columns]
    groups = [
        {
            **metadata_by_key[key],
            "observation_count": int(len(coverage.returns)),
        }
        for key in ordered_keys
    ]
    cells: list[list[dict[str, object]]] = []
    finite_values: list[float] = []
    for row_key in ordered_keys:
        row: list[dict[str, object]] = []
        for column_key in ordered_keys:
            value = _safe_float(correlation.loc[row_key, column_key])
            if value is not None:
                finite_values.append(value)
            row.append({"value": value, "observation_count": int(len(coverage.returns))})
        cells.append(row)
    return _ready_section(
        scope=scope,
        as_of_date=selected_date.isoformat(),
        available_as_of_dates=[item.isoformat() for item in dates],
        groups=groups,
        cells=cells,
        max_abs=max((abs(value) for value in finite_values), default=0.0),
        coverage={
            "observation_count": int(len(coverage.returns)),
            "rows_before_policy": int(coverage.rows_before),
            "rows_after_policy": int(coverage.rows_after),
            "missing_return_row_count": int(coverage.missing_row_count),
            "latest_complete_return_date": (
                coverage.latest_complete_date.isoformat() if coverage.latest_complete_date else None
            ),
        },
    )


def _risk_contribution_section(
    *,
    group_frame: pd.DataFrame,
    group_metadata: list[dict[str, object]],
    as_of_date: date,
    calculation_frequency: CalculationFrequency,
    risk_policy: dict[str, object],
) -> dict[str, object]:
    if group_frame.empty or not group_metadata:
        return _unavailable_section(
            _error_payload("Risk contribution requires grouped return history."),
            rows=[],
        )
    metadata_by_key = {str(item["key"]): item for item in group_metadata}
    ordered_keys = [str(column) for column in group_frame.columns]
    weights = np.asarray([float(metadata_by_key[key]["weight"]) for key in ordered_keys], dtype="float64")
    if not np.isfinite(weights).all() or float(np.abs(weights).sum()) <= _EPSILON:
        return _unavailable_section(_error_payload("Risk contribution weights are unavailable."), rows=[])
    try:
        result = estimate_risk_statistics(
            group_frame,
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
            risk_policy=risk_policy,
            label="Current portfolio risk contribution",
        )
        covariance: pd.DataFrame = result["covariance"]
        contribution = weighted_risk_contribution(
            covariance,
            weights,
            contribution_mode=str(risk_policy["contribution_mode"]),
        )
        covariance_values = np.asarray(contribution["covariance_values"], dtype="float64")
        signed = np.asarray(
            contribution["signed_contribution_to_variance"],
            dtype="float64",
        )
        variance = float(contribution["portfolio_variance"])
        shares = np.asarray(contribution["risk_shares"], dtype="float64")
    except ValueError as error:
        return _unavailable_section(_exception_error(error), rows=[])
    coverage = result["coverage"]
    rows = []
    for index, key in enumerate(ordered_keys):
        own_variance = float(covariance_values[index, index])
        rows.append(
            {
                "group_key": key,
                "group_label": str(metadata_by_key[key]["label"]),
                "weight": float(weights[index]),
                "annualized_volatility": sqrt(max(own_variance, 0.0)),
                "risk_share": float(shares[index]),
                "contribution_to_variance": float(signed[index]),
                "observation_count": int(len(coverage.returns)),
            }
        )
    rows.sort(key=lambda item: (-abs(float(item["risk_share"])), str(item["group_label"])))
    return _ready_section(
        rows=rows,
        portfolio_variance=variance,
        portfolio_volatility=sqrt(variance),
        observation_count=int(len(coverage.returns)),
    )


def _planning_groups(
    *,
    holdings: list[dict[str, object]],
    cash_accounts: tuple[PublishedCashAccountValue, ...],
    assignments: dict[tuple[str, str], dict[str, object]],
    nodes_by_id: dict[str, dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    entities: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for holding in holdings:
        value = _safe_float(holding.get("market_value_base"))
        if value is None:
            errors.append(_error_payload(f"Current market value is unavailable for {holding['label']}."))
            continue
        entities.append(
            {
                "scope": "instrument",
                "id": str(holding["instrument_id"]),
                "value": value,
                "cash_like": False,
            }
        )
    for cash_account in cash_accounts:
        account_id = cash_account.account_id
        value = _safe_float(cash_account.value_base_exact)
        if value is None:
            errors.append(_error_payload(f"Current account value is unavailable for cash account {account_id}."))
            continue
        if abs(value) <= 1e-9 and ("cash_bucket", account_id) not in assignments:
            continue
        entities.append({"scope": "cash_bucket", "id": account_id, "value": value, "cash_like": True})
    total = sum(float(item["value"]) for item in entities)
    if entities and total <= 1e-9:
        errors.append(_error_payload("Current target drift requires positive portfolio planning NAV."))
    grouped: dict[str, dict[str, object]] = {}
    for entity in entities:
        try:
            group_id, group_label = _scoped_group(
                target_scope=str(entity["scope"]),
                target_entity_id=str(entity["id"]),
                scope_node_id=None,
                assignments=assignments,
                nodes_by_id=nodes_by_id,
            )
        except ValueError as error:
            errors.append(_error_payload(str(error), reason_codes=["taxonomy_assignment_unavailable"]))
            continue
        current = grouped.setdefault(
            group_id,
            {
                "group_key": group_id,
                "label": group_label,
                "current_weight": 0.0,
                "current_value_base": 0.0,
                "has_market_risk_input": False,
                "has_cash_like_input": False,
            },
        )
        current["current_value_base"] = float(current["current_value_base"]) + float(entity["value"])
        current["current_weight"] = float(current["current_weight"]) + (
            float(entity["value"]) / total if total > 1e-9 else 0.0
        )
        current["has_cash_like_input"] = bool(current["has_cash_like_input"]) or bool(entity["cash_like"])
        current["has_market_risk_input"] = bool(current["has_market_risk_input"]) or not bool(entity["cash_like"])
    rows = sorted(
        grouped.values(),
        key=lambda item: (-abs(float(item["current_weight"])), str(item["label"])),
    )
    return rows, errors


def _target_rows(
    *,
    target_set: dict[str, object] | None,
    lines: list[dict[str, object]],
    current_groups: list[dict[str, object]],
    risk_share_by_group: dict[str, float],
    dimension: str,
    nodes_by_id: dict[str, dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if target_set is None:
        return [], []
    enabled_field = "weight_enabled" if dimension == "weight" else "risk_budget_enabled"
    value_field = "target_weight" if dimension == "weight" else "target_risk_share"
    if not bool(target_set.get(enabled_field)):
        return [], []
    if not lines:
        return [], [_error_payload(f"{target_set.get('name')} is active but has no target lines.")]
    errors: list[dict[str, object]] = []
    for line in lines:
        if str(line.get("target_member_type") or "") != "taxonomy_node":
            errors.append(_error_payload(f"{target_set.get('name')} root targets must use taxonomy nodes."))
            continue
        node_id = str(line.get("target_member_id") or "")
        node = nodes_by_id.get(node_id)
        if node is None:
            errors.append(_error_payload(f"{target_set.get('name')} references missing node {node_id}."))
        elif node.get("parent_taxonomy_node_id"):
            errors.append(_error_payload(f"{target_set.get('name')} root targets must reference top-level nodes."))
        if _safe_float(line.get(value_field)) is None:
            errors.append(_error_payload(f"{target_set.get('name')} is missing {value_field} for {node_id}."))
    target_total = sum(_safe_float(line.get(value_field)) or 0.0 for line in lines)
    if abs(target_total - 1.0) > 1e-6:
        errors.append(_error_payload(f"{target_set.get('name')} {value_field} values must sum to 100%."))
    if errors:
        return [], errors
    current_by_key = {str(item["group_key"]): item for item in current_groups}
    line_by_key = {str(item["target_member_id"]): item for item in lines}
    rows: list[dict[str, object]] = []
    for key in sorted(set(current_by_key) | set(line_by_key)):
        current_group = current_by_key.get(key)
        line = line_by_key.get(key)
        if dimension == "weight":
            current = _safe_float((current_group or {}).get("current_weight")) or 0.0
        elif key in risk_share_by_group:
            current = risk_share_by_group[key]
        elif current_group and bool(current_group.get("has_cash_like_input")) and not bool(current_group.get("has_market_risk_input")):
            current = 0.0
        elif current_group:
            errors.append(_error_payload(f"Current risk share is unavailable for {current_group.get('label')}."))
            continue
        else:
            current = 0.0
        target = _safe_float((line or {}).get(value_field)) or 0.0
        if abs(current) <= _EPSILON and abs(target) <= _EPSILON:
            continue
        node = nodes_by_id.get(key) or {}
        rows.append(
            {
                "key": key,
                "label": str((current_group or {}).get("label") or node.get("node_name") or key),
                "current": current,
                "target": target,
                "gap": current - target,
                "current_value_base": _safe_float((current_group or {}).get("current_value_base")),
            }
        )
    if errors:
        return [], errors
    rows.sort(key=lambda item: (-abs(float(item["gap"])), str(item["label"])))
    return rows, []


def _combine_target_rows(
    saa_rows: list[dict[str, object]],
    taa_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    saa_by_key = {str(item["key"]): item for item in saa_rows}
    taa_by_key = {str(item["key"]): item for item in taa_rows}
    result: list[dict[str, object]] = []
    for key in set(saa_by_key) | set(taa_by_key):
        saa = saa_by_key.get(key)
        taa = taa_by_key.get(key)
        current = _safe_float((saa or taa or {}).get("current"))
        saa_target = _safe_float((saa or {}).get("target"))
        taa_target = _safe_float((taa or {}).get("target"))
        result.append(
            {
                "id": f"target-gap:{key}",
                "label": str((saa or taa or {}).get("label") or key),
                "current": current,
                "saa_target": saa_target,
                "taa_target": taa_target,
                "saa_gap": current - saa_target if current is not None and saa_target is not None else None,
                "taa_gap": current - taa_target if current is not None and taa_target is not None else None,
                "current_value_base": _safe_float((saa or taa or {}).get("current_value_base")),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            -max(abs(_safe_float(item.get("saa_gap")) or 0.0), abs(_safe_float(item.get("taa_gap")) or 0.0)),
            str(item["label"]),
        ),
    )


def _drift_section(
    *,
    current_groups: list[dict[str, object]],
    planning_errors: list[dict[str, object]],
    target_sets: list[dict[str, object]],
    target_lines: list[dict[str, object]],
    risk_contribution: dict[str, object],
    nodes_by_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    root_sets = [
        item
        for item in target_sets
        if str(item.get("status") or "active") == "active" and not item.get("comparator_taxonomy_node_id")
    ]
    if not any(
        bool(item.get("weight_enabled")) or bool(item.get("risk_budget_enabled"))
        for item in root_sets
    ):
        return _not_applicable_section(weight_rows=[], risk_rows=[])
    if planning_errors:
        return _unavailable_section(*planning_errors, weight_rows=[], risk_rows=[])
    selected: dict[str, dict[str, object] | None] = {}
    errors: list[dict[str, object]] = []
    for target_type in ("saa", "taa"):
        matches = [item for item in root_sets if str(item.get("target_set_type") or "") == target_type]
        if len(matches) > 1:
            errors.append(_error_payload(f"Multiple active root {target_type.upper()} target sets are configured."))
        selected[target_type] = matches[0] if len(matches) == 1 else None
    if errors:
        return _unavailable_section(*errors, weight_rows=[], risk_rows=[])
    lines_by_set: dict[str, list[dict[str, object]]] = defaultdict(list)
    for line in target_lines:
        lines_by_set[str(line.get("target_set_id") or "")].append(line)
    risk_share_by_group = {
        str(item.get("group_key")): float(item["risk_share"])
        for item in list(risk_contribution.get("rows") or [])
        if isinstance(item, dict) and _safe_float(item.get("risk_share")) is not None
    }
    results: dict[tuple[str, str], list[dict[str, object]]] = {}
    for target_type in ("saa", "taa"):
        target_set = selected[target_type]
        target_set_id = str((target_set or {}).get("target_set_id") or "")
        for dimension in ("weight", "risk_budget"):
            rows, target_errors = _target_rows(
                target_set=target_set,
                lines=lines_by_set.get(target_set_id, []),
                current_groups=current_groups,
                risk_share_by_group=risk_share_by_group,
                dimension=dimension,
                nodes_by_id=nodes_by_id,
            )
            results[(target_type, dimension)] = rows
            errors.extend(target_errors)
    if str(risk_contribution.get("status")) != "ready" and any(
        bool(item.get("risk_budget_enabled")) for item in selected.values() if item
    ):
        errors.extend(list(risk_contribution.get("errors") or []))
    weight_rows = _combine_target_rows(results[("saa", "weight")], results[("taa", "weight")])
    risk_rows = _combine_target_rows(results[("saa", "risk_budget")], results[("taa", "risk_budget")])
    if errors:
        return _unavailable_section(*errors, weight_rows=weight_rows, risk_rows=[])
    return _ready_section(weight_rows=weight_rows, risk_rows=risk_rows)


def build_risk_workspace(
    portfolio_id: str,
    *,
    as_of_date: date,
    rolling_lookback_days: int,
    matrix_lookback_days: int,
    matrix_scope_node_id: str = ALL_INSTRUMENTS_SCOPE,
    matrix_as_of_date: date | None = None,
    benchmark_instrument_id: str | None = None,
) -> dict[str, object]:
    """Build Portfolio Risk from point-in-time facts and one canonical market-data lock."""

    rolling_window = _normalized_lookback(rolling_lookback_days, field_name="rolling_lookback_days")
    matrix_window = _normalized_lookback(matrix_lookback_days, field_name="matrix_lookback_days")
    if matrix_as_of_date and matrix_as_of_date > as_of_date:
        raise RiskWorkspaceRequestError("matrix_as_of_date cannot be later than as_of_date.")
    normalized_benchmark_id = str(benchmark_instrument_id or "").strip() or None

    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise RiskWorkspaceNotFoundError("Portfolio not found.")
    operating_profile = require_portfolio_operating_profile(
        portfolio.get("operating_profile"),
        context=f"Portfolio '{portfolio_id}'",
    )
    uses_allocation_policy = operating_profile == "standard_taxonomy"
    base_currency = require_portfolio_fact_currency(
        portfolio.get("base_currency"),
        context=f"Portfolio '{portfolio_id}' base",
    )
    try:
        statement = read_current_published_holdings(
            portfolio_id,
            as_of_date=as_of_date,
        )
    except PublishedHoldingsUnavailableError as error:
        raise RiskWorkspaceRequestError(str(error)) from error
    if statement.base_currency != base_currency:
        raise RiskWorkspaceRequestError(
            "Published holdings base currency does not match the portfolio fact."
        )
    holdings = _current_holding_records(statement)
    universe = _universe_records(list_portfolio_instrument_universe(portfolio_id), holdings)

    taxonomies = list_taxonomies(portfolio_id) if uses_allocation_policy else []
    taxonomy_id = (
        str(portfolio.get("default_planning_taxonomy_id") or "").strip()
        if uses_allocation_policy
        else ""
    )
    taxonomy = next(
        (
            item
            for item in taxonomies
            if str(item.get("taxonomy_id") or "") == taxonomy_id
            and str(item.get("status") or "active") == "active"
            and bool(item.get("planning_enabled"))
        ),
        None,
    )
    taxonomy_errors: list[dict[str, object]] = []
    if uses_allocation_policy and not taxonomy_id:
        taxonomy_errors.append(
            _error_payload(
                "Portfolio Risk requires a default planning taxonomy.",
                reason_codes=["planning_taxonomy_required"],
            )
        )
    elif uses_allocation_policy and taxonomy is None:
        taxonomy_errors.append(
            _error_payload(
                "The default planning taxonomy is missing, inactive, or not planning-enabled.",
                reason_codes=["planning_taxonomy_unavailable"],
            )
        )
    elif uses_allocation_policy and str(
        taxonomy.get("primary_assignment_scope") or ""
    ) != "instrument":
        taxonomy_errors.append(
            _error_payload(
                "Portfolio Risk requires an instrument-scoped planning taxonomy.",
                reason_codes=["instrument_taxonomy_required"],
            )
        )

    taxonomy_nodes = list_taxonomy_nodes(portfolio_id, taxonomy_id=taxonomy_id) if taxonomy_id else []
    active_nodes = {
        str(item.get("taxonomy_node_id") or ""): item
        for item in taxonomy_nodes
        if str(item.get("status") or "active") == "active"
    }
    try:
        assignments = _assignment_lookup(
            list_taxonomy_assignments(portfolio_id, taxonomy_id=taxonomy_id) if taxonomy_id else [],
            taxonomy_id=taxonomy_id,
        )
        scope_options = (
            _matrix_scope_options(active_nodes)
            if uses_allocation_policy and not taxonomy_errors
            else [
                {
                    "value": ALL_INSTRUMENTS_SCOPE,
                    "label": "All Instruments",
                    "kind": "instrument",
                }
            ]
        )
    except ValueError as error:
        assignments = {}
        scope_options = [{"value": ALL_INSTRUMENTS_SCOPE, "label": "All Instruments", "kind": "instrument"}]
        taxonomy_errors.append(_error_payload(str(error), reason_codes=["invalid_taxonomy_hierarchy"]))
    valid_scope_ids = {str(item["value"]) for item in scope_options}
    if matrix_scope_node_id not in valid_scope_ids:
        raise RiskWorkspaceRequestError("matrix_scope_node_id is not available in the planning taxonomy.")

    policy = normalize_portfolio_risk_policy(
        portfolio.get("risk_policy_json") if isinstance(portfolio.get("risk_policy_json"), dict) else None
    )
    all_instrument_ids = sorted(
        {
            *(str(item["instrument_id"]) for item in universe),
            *(str(item["instrument_id"]) for item in holdings),
            *([normalized_benchmark_id] if normalized_benchmark_id else []),
        }
    )
    nav_by_id: dict[str, pd.Series] = {}
    series_errors_by_id: dict[str, dict[str, object]] = {}
    series_warnings_by_id: dict[str, list[str]] = {}
    with get_session_factory()() as market_session:
        market_context = lock_portfolio_market_data_in_session(
            market_session,
            instrument_ids=all_instrument_ids,
            base_currency=base_currency,
            start_date=RISK_HISTORY_START_DATE,
            end_date=as_of_date,
        )
        for instrument_id in all_instrument_ids:
            try:
                nav, warnings = build_canonical_total_return_nav_series(
                    market_context,
                    instrument_id=instrument_id,
                    base_currency=base_currency,
                    start_date=RISK_HISTORY_START_DATE,
                    end_date=as_of_date,
                    warn_on_start_clip=False,
                )
                nav_by_id[instrument_id] = nav
                series_warnings_by_id[instrument_id] = warnings
            except (PortfolioMarketDataError, ValueError) as error:
                series_errors_by_id[instrument_id] = _exception_error(error)

        holding_frequencies = [
            infer_observation_frequency([pd.Timestamp(item).date() for item in nav_by_id[instrument_id].index])
            for instrument_id in (str(item["instrument_id"]) for item in holdings)
            if instrument_id in nav_by_id and len(nav_by_id[instrument_id]) >= 2
        ]
        try:
            frequency_profile = calculation_frequency_profile(
                requested_frequency=policy.get("calculation_frequency"),
                source_frequencies=holding_frequencies,
            )
            calculation_frequency = cast(
                CalculationFrequency,
                frequency_profile["resolved_frequency"],
            )
            frequency_error = None
        except ValueError as error:
            calculation_frequency = default_calculation_frequency(holding_frequencies)
            frequency_profile = {
                "requested_frequency": policy.get("calculation_frequency"),
                "resolved_frequency": calculation_frequency,
                "status_label": "Risk basis unavailable",
            }
            frequency_error = _exception_error(error)

        return_series_by_id: dict[str, pd.Series] = {}
        if frequency_error is None:
            for instrument_id, nav in nav_by_id.items():
                try:
                    return_series_by_id[instrument_id] = _returns_from_nav(
                        nav,
                        calculation_frequency=calculation_frequency,
                        as_of_date=as_of_date,
                    )
                except ValueError as error:
                    series_errors_by_id[instrument_id] = _exception_error(error)

        lineage = portfolio_market_data_manifest(market_context)

    model_snapshot = portfolio_risk_model_snapshot(
        policy,
        calculation_frequency=calculation_frequency,
    )
    holding_ids = [str(item["instrument_id"]) for item in holdings]
    holding_input_errors = [series_errors_by_id[item] for item in holding_ids if item in series_errors_by_id]
    missing_weights = [item for item in holdings if _safe_float(item.get("weight")) is None]
    holding_input_errors.extend(
        _error_payload(f"Current portfolio weight is unavailable for {item['label']}.") for item in missing_weights
    )
    if frequency_error is not None:
        holding_input_errors.append(frequency_error)

    rolling_policy = _policy_with_lookback(model_snapshot, rolling_window)
    if not holdings:
        rolling = _unavailable_section(
            _error_payload("Rolling risk requires at least one active non-cash holding."),
            lookback_days=rolling_window,
            model_id=str(model_snapshot["covariance_model_id"]),
            portfolio_volatility_points=[],
            portfolio_sharpe_points=[],
            benchmark_volatility_points=[],
            benchmark_sharpe_points=[],
        )
    elif holding_input_errors:
        rolling = _unavailable_section(
            *holding_input_errors,
            lookback_days=rolling_window,
            model_id=str(model_snapshot["covariance_model_id"]),
            portfolio_volatility_points=[],
            portfolio_sharpe_points=[],
            benchmark_volatility_points=[],
            benchmark_sharpe_points=[],
        )
    else:
        holding_frame = _return_frame(return_series_by_id, holding_ids)
        portfolio_returns = _portfolio_return_series(
            holding_frame,
            {str(item["instrument_id"]): float(item["weight"]) for item in holdings},
        )
        portfolio_volatility, portfolio_sharpe = _rolling_points(
            portfolio_returns,
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
            risk_policy=rolling_policy,
            label="portfolio",
        )
        benchmark_volatility: list[dict[str, object]] = []
        benchmark_sharpe: list[dict[str, object]] = []
        rolling_errors: list[dict[str, object]] = []
        if normalized_benchmark_id:
            if normalized_benchmark_id in series_errors_by_id:
                rolling_errors.append(series_errors_by_id[normalized_benchmark_id])
            else:
                benchmark_volatility, benchmark_sharpe = _rolling_points(
                    return_series_by_id.get(normalized_benchmark_id, pd.Series(dtype="float64")),
                    as_of_date=as_of_date,
                    calculation_frequency=calculation_frequency,
                    risk_policy=rolling_policy,
                    label="benchmark",
                )
        if not portfolio_volatility:
            rolling_errors.append(
                _error_payload(
                    "Rolling risk has no complete calculable window.",
                    reason_codes=["insufficient_rolling_history"],
                )
            )
        rolling = (
            _unavailable_section(
                *rolling_errors,
                lookback_days=rolling_window,
                model_id=str(model_snapshot["covariance_model_id"]),
                portfolio_volatility_points=portfolio_volatility,
                portfolio_sharpe_points=portfolio_sharpe,
                benchmark_volatility_points=benchmark_volatility,
                benchmark_sharpe_points=benchmark_sharpe,
            )
            if rolling_errors
            else _ready_section(
                lookback_days=rolling_window,
                model_id=str(model_snapshot["covariance_model_id"]),
                portfolio_volatility_points=portfolio_volatility,
                portfolio_sharpe_points=portfolio_sharpe,
                benchmark_volatility_points=benchmark_volatility,
                benchmark_sharpe_points=benchmark_sharpe,
            )
        )

    instrument_frame = _return_frame(
        return_series_by_id,
        [instrument_id for instrument_id in holding_ids if instrument_id in return_series_by_id],
    )
    top_group_frame = pd.DataFrame()
    top_group_metadata: list[dict[str, object]] = []
    group_errors = [*holding_input_errors]
    if not uses_allocation_policy and not group_errors:
        top_group_frame = instrument_frame
        top_group_metadata = [
            {
                "key": str(item["instrument_id"]),
                "label": str(item["label"]),
                "weight": float(item["weight"]),
            }
            for item in holdings
            if str(item["instrument_id"]) in instrument_frame.columns
        ]
    elif uses_allocation_policy:
        group_errors = [*taxonomy_errors, *group_errors]
    if uses_allocation_policy and not group_errors:
        top_group_frame, top_group_metadata, group_errors = _taxonomy_group_frame(
            instrument_frame=instrument_frame,
            holdings=holdings,
            scope_node_id=None,
            assignments=assignments,
            nodes_by_id=active_nodes,
        )
    risk_contribution = (
        _unavailable_section(*group_errors, rows=[])
        if group_errors
        else _risk_contribution_section(
            group_frame=top_group_frame,
            group_metadata=top_group_metadata,
            as_of_date=as_of_date,
            calculation_frequency=calculation_frequency,
            risk_policy=model_snapshot,
        )
    )
    matrix_policy = _policy_with_lookback(model_snapshot, matrix_window)
    if frequency_error is not None:
        matrix = _unavailable_section(
            frequency_error,
            scope=matrix_scope_node_id,
            as_of_date=None,
            available_as_of_dates=[],
            groups=[],
            cells=[],
            max_abs=0.0,
        )
    elif matrix_scope_node_id == ALL_INSTRUMENTS_SCOPE:
        universe_ids = [str(item["instrument_id"]) for item in universe]
        universe_errors = [series_errors_by_id[item] for item in universe_ids if item in series_errors_by_id]
        if universe_errors:
            matrix = _unavailable_section(
                *universe_errors,
                scope=matrix_scope_node_id,
                as_of_date=None,
                available_as_of_dates=[],
                groups=[],
                cells=[],
                max_abs=0.0,
            )
        else:
            matrix_frame = _return_frame(return_series_by_id, universe_ids)
            weight_by_id = {str(item["instrument_id"]): _safe_float(item.get("weight")) or 0.0 for item in holdings}
            matrix = _build_matrix_section(
                frame=matrix_frame,
                metadata=[
                    {
                        "key": str(item["instrument_id"]),
                        "label": str(item["label"]),
                        "weight": weight_by_id.get(str(item["instrument_id"]), 0.0),
                    }
                    for item in universe
                ],
                requested_as_of_date=matrix_as_of_date,
                calculation_frequency=calculation_frequency,
                risk_policy=matrix_policy,
                scope=matrix_scope_node_id,
            )
    elif taxonomy_errors or holding_input_errors:
        matrix = _unavailable_section(
            *(taxonomy_errors + holding_input_errors),
            scope=matrix_scope_node_id,
            as_of_date=None,
            available_as_of_dates=[],
            groups=[],
            cells=[],
            max_abs=0.0,
        )
    else:
        scoped_frame, scoped_metadata, scoped_errors = _taxonomy_group_frame(
            instrument_frame=instrument_frame,
            holdings=holdings,
            scope_node_id=(
                None
                if matrix_scope_node_id == TOP_LEVEL_TAXONOMY_SCOPE
                else matrix_scope_node_id
            ),
            assignments=assignments,
            nodes_by_id=active_nodes,
        )
        matrix = (
            _unavailable_section(
                *scoped_errors,
                scope=matrix_scope_node_id,
                as_of_date=None,
                available_as_of_dates=[],
                groups=[],
                cells=[],
                max_abs=0.0,
            )
            if scoped_errors
            else _build_matrix_section(
                frame=scoped_frame,
                metadata=scoped_metadata,
                requested_as_of_date=matrix_as_of_date,
                calculation_frequency=calculation_frequency,
                risk_policy=matrix_policy,
                scope=matrix_scope_node_id,
            )
        )

    if uses_allocation_policy:
        current_groups, planning_errors = (
            _planning_groups(
                holdings=holdings,
                cash_accounts=statement.cash_accounts,
                assignments=assignments,
                nodes_by_id=active_nodes,
            )
            if not taxonomy_errors
            else ([], list(taxonomy_errors))
        )
        target_sets = (
            list_target_sets(portfolio_id, taxonomy_id=taxonomy_id)
            if taxonomy_id
            else []
        )
        target_lines = (
            list_target_set_lines(portfolio_id, taxonomy_id=taxonomy_id)
            if taxonomy_id
            else []
        )
        allocation_policy_drift = _drift_section(
            current_groups=current_groups,
            planning_errors=planning_errors,
            target_sets=target_sets,
            target_lines=target_lines,
            risk_contribution=risk_contribution,
            nodes_by_id=active_nodes,
        )
    else:
        allocation_policy_drift = _not_applicable_section(
            weight_rows=[],
            risk_rows=[],
        )

    coverage_rows = []
    holding_id_set = set(holding_ids)
    universe_id_set = {str(item["instrument_id"]) for item in universe}
    labels = {str(item["instrument_id"]): str(item["label"]) for item in universe}
    labels.update({str(item["instrument_id"]): str(item["label"]) for item in holdings})
    for instrument_id in all_instrument_ids:
        nav = nav_by_id.get(instrument_id, pd.Series(dtype="float64"))
        scopes = []
        if instrument_id in holding_id_set:
            scopes.append("holding")
        if instrument_id in universe_id_set:
            scopes.append("universe")
        if instrument_id == normalized_benchmark_id:
            scopes.append("benchmark")
        error = series_errors_by_id.get(instrument_id)
        coverage_rows.append(
            {
                "instrument_id": instrument_id,
                "label": labels.get(instrument_id, instrument_id),
                "scopes": scopes,
                "status": "unavailable" if error else "ready",
                "observation_count": int(len(nav)),
                "first_observation_date": (
                    pd.Timestamp(nav.index[0]).date().isoformat() if not nav.empty else None
                ),
                "last_observation_date": (
                    pd.Timestamp(nav.index[-1]).date().isoformat() if not nav.empty else None
                ),
                "warnings": series_warnings_by_id.get(instrument_id, []),
                "errors": [error] if error else [],
            }
        )

    sections = [rolling, matrix, risk_contribution, allocation_policy_drift]
    applicable_sections = [
        section for section in sections if section.get("status") != "not_applicable"
    ]
    ready_count = sum(
        1 for section in applicable_sections if section.get("status") == "ready"
    )
    status = (
        "ready"
        if ready_count == len(applicable_sections)
        else "partial"
        if ready_count
        else "unavailable"
    )
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": base_currency,
        "operating_profile": operating_profile,
        "as_of_date": as_of_date.isoformat(),
        "status": status,
        "planning_taxonomy": (
            {
                "taxonomy_id": taxonomy_id,
                "name": str((taxonomy or {}).get("name") or taxonomy_id),
            }
            if taxonomy
            else None
        ),
        "risk_policy": model_snapshot,
        "frequency_profile": frequency_profile,
        "matrix_scope_options": scope_options,
        "rolling": rolling,
        "matrix": matrix,
        "risk_contribution": risk_contribution,
        "allocation_policy_drift": allocation_policy_drift,
        "coverage": {
            "market_data_role": "total_return",
            "instrument_count": len(coverage_rows),
            "ready_instrument_count": sum(1 for item in coverage_rows if item["status"] == "ready"),
            "instruments": coverage_rows,
        },
        "calculation_lineage": {
            "engine_version": RISK_WORKSPACE_ENGINE_VERSION,
            "mathematical_kernel": "portfolio_app.services.risk_model + portfolio_app.services.risk_math",
            "operating_profile": operating_profile,
            "as_of_date": as_of_date.isoformat(),
            "rolling_lookback_days": rolling_window,
            "rolling_output_point_limit": MAX_ROLLING_OUTPUT_POINTS,
            "matrix_lookback_days": matrix_window,
            "matrix_as_of_option_limit": MAX_MATRIX_AS_OF_OPTIONS,
            "matrix_as_of_date": matrix_as_of_date.isoformat() if matrix_as_of_date else None,
            "matrix_scope_node_id": matrix_scope_node_id,
            "benchmark_instrument_id": normalized_benchmark_id,
            "holdings_source": "point-in-time portfolio ledger replay",
            "instrument_universe_basis": "current active portfolio instrument universe",
            "planning_taxonomy_basis": (
                "current active unversioned planning taxonomy and assignments"
                if uses_allocation_policy
                else "not_applicable_external_etf_rotation"
            ),
            "planning_target_basis": (
                "current active unversioned target sets"
                if uses_allocation_policy
                else "not_applicable_external_etf_rotation"
            ),
            "portfolio_return_weighting": "current_static_weights",
            "taxonomy_group_return_weighting": (
                "current_static_weights_normalized_within_group"
                if uses_allocation_policy
                else "not_applicable_external_etf_rotation"
            ),
            "taxonomy_id": taxonomy_id or None,
            "risk_policy_role": "production",
            "covariance_model_id": str(model_snapshot["covariance_model_id"]),
            "missing_return_policy": str(model_snapshot["missing_return_policy"]),
            "contribution_mode": str(model_snapshot["contribution_mode"]),
            "resolved_calculation_frequency": calculation_frequency,
        },
        "data_lineage": lineage,
    }


__all__ = [
    "ALL_INSTRUMENTS_SCOPE",
    "RISK_WORKSPACE_ENGINE_VERSION",
    "TOP_LEVEL_TAXONOMY_SCOPE",
    "RiskWorkspaceNotFoundError",
    "RiskWorkspaceRequestError",
    "build_risk_workspace",
]
