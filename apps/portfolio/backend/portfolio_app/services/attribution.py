"""Pure contribution grouping, daily-slice reduction, and realized attribution.

This module deliberately accepts already-built portfolio facts and daily slices.
It does not load registry data, replay the ledger, value holdings, or build
snapshots.  Those orchestration and monkeypatch boundaries remain in the
portfolio performance orchestration layer.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from math import isfinite, prod, sqrt
from typing import cast

from portfolio_app.services import period_metrics, valuation_fx
from portfolio_app.services.calculation_frequency import CalculationFrequency, period_end_date


CONTRIBUTION_AXES = {"instrument", "account", "instrument_type", "currency", "taxonomy"}
CONTRIBUTION_BASE_AXES = {"instrument", "account", "instrument_type", "currency"}
CONTRIBUTION_AXIS_ERROR = "axis must be instrument, account, instrument_type, currency, or taxonomy"
MATERIALIZED_CONTRIBUTION_AXES: tuple[str, ...] = (
    "instrument",
    "account",
    "instrument_type",
    "currency",
    "cash_detail",
    "instrument_detail",
    "account_detail",
    "instrument_type_detail",
    "currency_detail",
)
GROUP_CAPITAL_FLOW_IN_FIELD = "capital_flow_in_base"
GROUP_CAPITAL_FLOW_OUT_FIELD = "capital_flow_out_base"

_CALCULATION_DETAIL_SUFFIX = "_detail"
_CALCULATION_DETAIL_GROUP_SEPARATOR = "\x1f"
_CALCULATION_DETAIL_PARENT_AXES = {
    "instrument",
    "account",
    "instrument_type",
    "currency",
    "taxonomy",
}
_CALCULATION_CASH_DETAIL_AXIS = "cash_detail"
_CALCULATION_DETAIL_ADDITIVE_SLICE_FIELDS = (
    "beginning_value_base",
    "ending_value_base",
    "beginning_weight",
    "ending_weight",
    "cash_balance_base",
    "position_market_value_base",
    "open_cost_basis_base",
    "realized_pnl",
    "unrealized_pnl",
    "unrealized_pnl_change",
    "income_cash_amount",
    "expense_cash_amount",
    "fee_amount",
    "tax_amount",
    "cash_currency_gains",
    "pending_settlement_currency_gains",
    "instrument_currency_gains",
    GROUP_CAPITAL_FLOW_IN_FIELD,
    GROUP_CAPITAL_FLOW_OUT_FIELD,
    "total_pnl",
    "daily_contribution",
)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_initial_slice_value(
    daily_slice: dict[str, object],
    *,
    value_field: str,
    anchor_value_field: str,
) -> object:
    """Select the economically valid start boundary for a period slice.

    Explicit close-to-close starts and imported opening rows are EOD valuation
    anchors, not return subperiods. Their ending value/weight is therefore the
    period's opening boundary. Every normal row starts at its BOD value/weight.
    """

    return daily_slice.get(
        anchor_value_field
        if bool(daily_slice.get("_is_initial_valuation_anchor"))
        else value_field
    )


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _iter_dates(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    span = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(span + 1)]


def _snapshot_as_close_boundary(
    snapshot: dict[str, object],
) -> dict[str, object]:
    boundary = dict(snapshot)
    ending_nav = _safe_float(snapshot.get("ending_nav"))
    if ending_nav is None:
        ending_nav = _safe_float(snapshot.get("nav"))
    boundary["_is_initial_valuation_anchor"] = True
    boundary["_is_period_start_close_anchor"] = True
    boundary["beginning_nav"] = ending_nav
    boundary["ending_nav"] = ending_nav
    boundary["nav"] = ending_nav
    boundary["external_cash_in"] = 0.0
    boundary["external_cash_out"] = 0.0
    boundary["net_external_inflow"] = 0.0
    boundary["absolute_change"] = 0.0 if ending_nav is not None else None
    boundary["delta"] = 0.0 if ending_nav is not None else None
    boundary["daily_twr"] = None
    boundary["return_observation_eligible"] = False
    boundary["return_chain_continuous"] = ending_nav is not None
    return boundary


def _slice_as_close_boundary(
    daily_slice: dict[str, object],
) -> dict[str, object]:
    boundary = dict(daily_slice)
    boundary["_is_initial_valuation_anchor"] = True
    boundary["_is_period_start_close_anchor"] = True
    boundary["beginning_value_base"] = daily_slice.get("ending_value_base")
    boundary["beginning_weight"] = daily_slice.get("ending_weight")
    boundary["daily_return"] = None
    boundary["return_observation_eligible"] = False
    for field_name in (
        "realized_pnl",
        "unrealized_pnl_change",
        "income_cash_amount",
        "expense_cash_amount",
        "fee_amount",
        "tax_amount",
        "cash_currency_gains",
        "pending_settlement_currency_gains",
        "instrument_currency_gains",
        GROUP_CAPITAL_FLOW_IN_FIELD,
        GROUP_CAPITAL_FLOW_OUT_FIELD,
        "total_pnl",
    ):
        boundary[field_name] = 0.0
    if (
        axis_includes_cash_balance(str(daily_slice.get("axis") or ""))
        and _safe_float(
            daily_slice.get("pending_settlement_currency_gains")
        )
        is None
    ):
        # A close boundary removes the start day's known additive movement,
        # but it must not turn an unknown monetary-FX component into a proven
        # zero.  The line accumulator uses this missing component to keep the
        # dependent total P&L and contribution decomposition fail-closed.
        boundary["pending_settlement_currency_gains"] = None
    # The start close is a valuation state, not a return/contribution
    # observation.  Keep additive bridge fields at zero, while preserving the
    # distinction between "zero contribution" and "no subperiod".
    boundary["daily_contribution"] = None
    return boundary


def calculation_detail_axis(axis: str) -> str:
    return f"{axis}{_CALCULATION_DETAIL_SUFFIX}"


def calculation_detail_parent_axis(axis: str) -> str | None:
    if not axis.endswith(_CALCULATION_DETAIL_SUFFIX):
        return None
    parent_axis = axis[: -len(_CALCULATION_DETAIL_SUFFIX)]
    return parent_axis if parent_axis in _CALCULATION_DETAIL_PARENT_AXES else None


def is_internal_calculation_axis(axis: str) -> bool:
    return (
        axis == _CALCULATION_CASH_DETAIL_AXIS
        or calculation_detail_parent_axis(axis) in CONTRIBUTION_BASE_AXES
    )


def encode_calculation_detail_group_key(
    *,
    parent_group_key: str,
    item_kind: str,
    item_key: str,
) -> str:
    return _CALCULATION_DETAIL_GROUP_SEPARATOR.join(
        [parent_group_key, item_kind, item_key]
    )


def decode_calculation_detail_group_key(value: object) -> tuple[str, str, str] | None:
    parts = str(value or "").split(_CALCULATION_DETAIL_GROUP_SEPARATOR, 2)
    if len(parts) != 3:
        return None
    parent_group_key, item_kind, item_key = parts
    if not parent_group_key or item_kind not in {"instrument", "cash"} or not item_key:
        return None
    return (parent_group_key, item_kind, item_key)


def cash_detail_item_key(*, account_id: str, currency: str) -> str:
    return f"cash:{account_id or 'unassigned'}:{currency or 'unassigned'}"


def cash_detail_item_label(
    *,
    parent_axis: str,
    account_id: str,
    currency: str,
    account_name_map: dict[str, str],
) -> str:
    if parent_axis == "account":
        return f"Cash ({currency})" if currency else "Cash"
    account_label = account_name_map.get(account_id, account_id)
    if account_label and currency:
        return f"{account_label} ({currency})"
    return account_label or "Cash"


def account_name_map(accounts: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(account.get("account_id") or ""): str(
            account.get("account_name") or account.get("account_id") or ""
        )
        for account in accounts
    }


def axis_includes_cash_balance(axis: str) -> bool:
    if axis == _CALCULATION_CASH_DETAIL_AXIS:
        return True
    parent_axis = calculation_detail_parent_axis(axis)
    if parent_axis is not None:
        return parent_axis in {"instrument", "account", "instrument_type", "currency"}
    return axis in {"instrument", "account", "instrument_type", "currency"}


def instrument_type_key_label(value: object) -> tuple[str, str]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ("unassigned:instrument_type", "Unassigned")
    group_key = raw_value.replace(" ", "_").replace("-", "_").lower()
    group_label = " ".join(part.capitalize() for part in group_key.split("_") if part)
    return (group_key, group_label or raw_value)


def instrument_ref_from_mapping(item: dict[str, object]) -> dict[str, object]:
    instrument_ref = item.get("instrument_ref")
    return instrument_ref if isinstance(instrument_ref, dict) else {}


def position_group_for_axis(
    *,
    axis: str,
    position_lot: dict[str, object],
    account_name_map: dict[str, str],
    base_currency: str,
) -> tuple[str, str]:
    if axis == _CALCULATION_CASH_DETAIL_AXIS:
        return ("", "")
    detail_parent_axis = calculation_detail_parent_axis(axis)
    if detail_parent_axis in {"instrument", "account", "instrument_type", "currency"}:
        parent_group_key, _parent_group_label = position_group_for_axis(
            axis=detail_parent_axis,
            position_lot=position_lot,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        item_key, item_label = position_group_for_axis(
            axis="instrument",
            position_lot=position_lot,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        return (
            encode_calculation_detail_group_key(
                parent_group_key=parent_group_key,
                item_kind="instrument",
                item_key=item_key,
            ),
            item_label,
        )
    if axis == "instrument":
        group_key = str(position_lot.get("instrument_id") or "")
        instrument_ref = instrument_ref_from_mapping(position_lot)
        return (group_key, str(instrument_ref.get("instrument_name") or group_key))
    if axis == "account":
        group_key = str(position_lot.get("account_id") or "")
        return (group_key, account_name_map.get(group_key, group_key))
    if axis == "instrument_type":
        return instrument_type_key_label(
            instrument_ref_from_mapping(position_lot).get("instrument_type")
        )
    if axis == "currency":
        group_key = valuation_fx.required_currency(
            position_lot.get("currency"), field_name="position-lot currency"
        )
        return (group_key, group_key)
    raise ValueError(CONTRIBUTION_AXIS_ERROR)


def cash_group_for_axis(
    *,
    axis: str,
    account_id: str,
    currency: str,
    account_name_map: dict[str, str],
) -> tuple[str, str]:
    if axis == _CALCULATION_CASH_DETAIL_AXIS:
        return (
            encode_calculation_detail_group_key(
                parent_group_key="cash",
                item_kind="cash",
                item_key=cash_detail_item_key(account_id=account_id, currency=currency),
            ),
            cash_detail_item_label(
                parent_axis="instrument",
                account_id=account_id,
                currency=currency,
                account_name_map=account_name_map,
            ),
        )
    detail_parent_axis = calculation_detail_parent_axis(axis)
    if detail_parent_axis in {"instrument", "account", "instrument_type", "currency"}:
        parent_group_key, _parent_group_label = cash_group_for_axis(
            axis=detail_parent_axis,
            account_id=account_id,
            currency=currency,
            account_name_map=account_name_map,
        )
        return (
            encode_calculation_detail_group_key(
                parent_group_key=parent_group_key,
                item_kind="cash",
                item_key=cash_detail_item_key(account_id=account_id, currency=currency),
            ),
            cash_detail_item_label(
                parent_axis=detail_parent_axis,
                account_id=account_id,
                currency=currency,
                account_name_map=account_name_map,
            ),
        )
    if axis == "instrument":
        return ("cash", "Cash")
    if axis == "account":
        return (account_id, account_name_map.get(account_id, account_id))
    if axis == "instrument_type":
        return ("cash", "Cash")
    if axis == "currency":
        return (currency, currency)
    raise ValueError(CONTRIBUTION_AXIS_ERROR)


def transaction_group_for_axis(
    *,
    axis: str,
    transaction: dict[str, object],
    account_name_map: dict[str, str],
    base_currency: str,
) -> tuple[str, str]:
    account_id = str(transaction.get("account_id") or "")
    instrument_ref = instrument_ref_from_mapping(transaction)
    instrument_id = str(
        transaction.get("instrument_id") or instrument_ref.get("instrument_id") or ""
    )
    currency = valuation_fx.required_currency(
        transaction.get("currency"), field_name="transaction currency"
    )
    if axis == _CALCULATION_CASH_DETAIL_AXIS:
        if instrument_id:
            return ("", "")
        return (
            encode_calculation_detail_group_key(
                parent_group_key="cash",
                item_kind="cash",
                item_key=cash_detail_item_key(account_id=account_id, currency=currency),
            ),
            cash_detail_item_label(
                parent_axis="instrument",
                account_id=account_id,
                currency=currency,
                account_name_map=account_name_map,
            ),
        )
    detail_parent_axis = calculation_detail_parent_axis(axis)
    if detail_parent_axis in {"instrument", "account", "instrument_type", "currency"}:
        parent_group_key, _parent_group_label = transaction_group_for_axis(
            axis=detail_parent_axis,
            transaction=transaction,
            account_name_map=account_name_map,
            base_currency=base_currency,
        )
        if instrument_id:
            item_kind = "instrument"
            item_key = instrument_id
            item_label = str(instrument_ref.get("instrument_name") or instrument_id)
        else:
            item_kind = "cash"
            item_key = cash_detail_item_key(account_id=account_id, currency=currency)
            item_label = cash_detail_item_label(
                parent_axis=detail_parent_axis,
                account_id=account_id,
                currency=currency,
                account_name_map=account_name_map,
            )
        return (
            encode_calculation_detail_group_key(
                parent_group_key=parent_group_key,
                item_kind=item_kind,
                item_key=item_key,
            ),
            item_label,
        )
    if axis == "instrument":
        if not instrument_id:
            return ("cash", "Cash")
        return (instrument_id, str(instrument_ref.get("instrument_name") or instrument_id))
    if axis == "account":
        return (account_id, account_name_map.get(account_id, account_id))
    if axis == "instrument_type":
        if instrument_id:
            return instrument_type_key_label(instrument_ref.get("instrument_type"))
        return ("cash", "Cash")
    if axis == "currency":
        return (currency, currency)
    raise ValueError(CONTRIBUTION_AXIS_ERROR)


def cash_bucket_account_ids(accounts: list[dict[str, object]]) -> set[str]:
    return {
        str(account.get("account_id") or "")
        for account in accounts
        if str(account.get("account_id") or "")
        and str(account.get("account_type") or "") == "deposit_account"
    }


def daily_group_return_from_components(
    *,
    beginning_value_base: float | None,
    ending_value_base: float | None,
    total_pnl: float | None,
    capital_flow_in_base: float | None,
    capital_flow_out_base: float | None,
) -> float | None:
    if (
        beginning_value_base is None
        or ending_value_base is None
        or total_pnl is None
        or capital_flow_in_base is None
        or capital_flow_out_base is None
    ):
        return None

    implied_capital_flow_in = 0.0
    implied_net_flow = ending_value_base - beginning_value_base - total_pnl
    if implied_net_flow > 1e-9:
        implied_capital_flow_in = implied_net_flow
    return_denominator = beginning_value_base + max(
        capital_flow_in_base,
        implied_capital_flow_in,
    )
    if return_denominator <= 1e-9:
        return None
    return total_pnl / return_denominator


def merge_group_coverage_state(states: list[str]) -> str:
    normalized_states = [state for state in states if state]
    if not normalized_states:
        return "unavailable"
    if all(state == "complete" for state in normalized_states):
        return "complete"
    if any(state in {"complete", "partial"} for state in normalized_states):
        return "partial"
    return "unavailable"


def resolve_taxonomy_group_for_date(
    *,
    taxonomy: dict[str, object],
    taxonomy_nodes_by_id: dict[str, dict[str, object]],
    assignments_by_entity: dict[str, list[dict[str, object]]],
    target_entity_id: str,
    as_of_date: date,
) -> tuple[str, str]:
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    active_assignments = [
        assignment
        for assignment in assignments_by_entity.get(target_entity_id, [])
        if str(assignment.get("status") or "active") == "active"
    ]
    if len(active_assignments) > 1:
        raise ValueError(
            f"Multiple active taxonomy assignments overlap for {target_entity_id} on {as_of_date.isoformat()}."
        )
    if not active_assignments:
        return (f"unassigned:{taxonomy_id}", "Unassigned")

    assignment = active_assignments[0]
    taxonomy_node_id = str(assignment.get("taxonomy_node_id") or "")
    taxonomy_node = taxonomy_nodes_by_id.get(taxonomy_node_id)
    if taxonomy_node is None:
        raise ValueError(f"Taxonomy assignment references missing node {taxonomy_node_id}.")
    if str(taxonomy_node.get("status") or "active") != "active":
        raise ValueError(f"Taxonomy node {taxonomy_node_id} is not active.")
    return (
        taxonomy_node_id,
        str(taxonomy_node.get("node_name") or taxonomy_node_id),
    )


def is_taxonomy_unassigned_group(group_key: str, taxonomy_id: str) -> bool:
    return group_key == f"unassigned:{taxonomy_id}"


def daily_slice_has_period_end_exposure(daily_slice: dict[str, object]) -> bool:
    for field_name in (
        "ending_value_base",
        "position_market_value_base",
        "open_cost_basis_base",
        "cash_balance_base",
    ):
        value = _safe_float(daily_slice.get(field_name))
        if value is not None and abs(value) > 1e-9:
            return True
    return False


def resolve_period_taxonomy_group_for_slice(
    *,
    taxonomy: dict[str, object],
    taxonomy_nodes_by_id: dict[str, dict[str, object]],
    assignments_by_entity: dict[str, list[dict[str, object]]],
    target_scope: str,
    target_entity_id: str,
    slice_date: date,
    assignment_as_of_date: date | None,
    entities_present_at_assignment_date: set[str],
) -> tuple[str, str]:
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    taxonomy_group_key, taxonomy_group_label = resolve_taxonomy_group_for_date(
        taxonomy=taxonomy,
        taxonomy_nodes_by_id=taxonomy_nodes_by_id,
        assignments_by_entity=assignments_by_entity,
        target_entity_id=target_entity_id,
        as_of_date=assignment_as_of_date or slice_date,
    )
    if (
        assignment_as_of_date is not None
        and target_scope == "instrument"
        and target_entity_id not in entities_present_at_assignment_date
        and is_taxonomy_unassigned_group(taxonomy_group_key, taxonomy_id)
    ):
        fallback_group_key, fallback_group_label = resolve_taxonomy_group_for_date(
            taxonomy=taxonomy,
            taxonomy_nodes_by_id=taxonomy_nodes_by_id,
            assignments_by_entity=assignments_by_entity,
            target_entity_id=target_entity_id,
            as_of_date=slice_date,
        )
        if not is_taxonomy_unassigned_group(fallback_group_key, taxonomy_id):
            return (fallback_group_key, fallback_group_label)
    return (taxonomy_group_key, taxonomy_group_label)


def group_contribution_slices_by_taxonomy(
    *,
    taxonomy: dict[str, object],
    taxonomy_nodes: list[dict[str, object]],
    taxonomy_assignments: list[dict[str, object]],
    base_daily_slices: list[dict[str, object]],
    assignment_as_of_date: date | None = None,
    preserve_cash_group: bool = False,
) -> list[dict[str, object]]:
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    target_scope = str(taxonomy.get("primary_assignment_scope") or "")
    taxonomy_nodes_by_id = {
        str(node.get("taxonomy_node_id") or ""): node
        for node in taxonomy_nodes
        if str(node.get("taxonomy_id") or "") == taxonomy_id
    }
    assignments_by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for assignment in taxonomy_assignments:
        if str(assignment.get("taxonomy_id") or "") != taxonomy_id:
            continue
        if str(assignment.get("target_scope") or "") != target_scope:
            continue
        assignments_by_entity[str(assignment.get("target_entity_id") or "")].append(
            assignment
        )
    for entity_assignments in assignments_by_entity.values():
        entity_assignments.sort(
            key=lambda item: (
                str(item.get("assignment_id") or ""),
            )
        )

    grouped: dict[tuple[date, str], dict[str, object]] = {}
    coverage_states_by_group: dict[tuple[date, str], list[str]] = defaultdict(list)
    entities_present_at_assignment_date: set[str] = set()
    if assignment_as_of_date is not None:
        for base_slice in base_daily_slices:
            as_of_date = base_slice.get("as_of_date")
            if as_of_date != assignment_as_of_date:
                continue
            base_group_key = str(base_slice.get("group_key") or "")
            if base_group_key and daily_slice_has_period_end_exposure(base_slice):
                entities_present_at_assignment_date.add(base_group_key)

    for base_slice in base_daily_slices:
        as_of_date = base_slice.get("as_of_date")
        if not isinstance(as_of_date, date):
            continue
        base_group_key = str(base_slice.get("group_key") or "")
        if preserve_cash_group and target_scope == "instrument" and base_group_key == "cash":
            taxonomy_group_key, taxonomy_group_label = ("cash", "Cash")
        else:
            taxonomy_group_key, taxonomy_group_label = (
                resolve_period_taxonomy_group_for_slice(
                    taxonomy=taxonomy,
                    taxonomy_nodes_by_id=taxonomy_nodes_by_id,
                    assignments_by_entity=assignments_by_entity,
                    target_scope=target_scope,
                    target_entity_id=base_group_key,
                    slice_date=as_of_date,
                    assignment_as_of_date=assignment_as_of_date,
                    entities_present_at_assignment_date=entities_present_at_assignment_date,
                )
            )
        slice_key = (as_of_date, taxonomy_group_key)
        grouped_slice = grouped.setdefault(
            slice_key,
            {
                "as_of_date": as_of_date,
                "axis": "taxonomy",
                "group_key": taxonomy_group_key,
                "group_label": taxonomy_group_label,
                "coverage_state": "complete",
                "market_observation_count": 0,
                "_is_initial_valuation_anchor": False,
                "beginning_value_base": 0.0,
                "ending_value_base": 0.0,
                "beginning_weight": 0.0,
                "ending_weight": 0.0,
                "cash_balance_base": 0.0,
                "position_market_value_base": 0.0,
                "open_cost_basis_base": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "pending_settlement_currency_gains": 0.0,
                "instrument_currency_gains": 0.0,
                GROUP_CAPITAL_FLOW_IN_FIELD: 0.0,
                GROUP_CAPITAL_FLOW_OUT_FIELD: 0.0,
                "total_pnl": 0.0,
                "daily_return": None,
                "daily_contribution": 0.0,
                "return_observation_eligible": False,
            },
        )
        coverage_states_by_group[slice_key].append(
            str(base_slice.get("coverage_state") or "unavailable")
        )
        grouped_slice["market_observation_count"] = (
            int(grouped_slice.get("market_observation_count") or 0)
            + int(base_slice.get("market_observation_count") or 0)
        )
        grouped_slice["_is_initial_valuation_anchor"] = bool(
            grouped_slice.get("_is_initial_valuation_anchor")
            or base_slice.get("_is_initial_valuation_anchor")
        )

        for field_name in (
            "beginning_value_base",
            "ending_value_base",
            "beginning_weight",
            "ending_weight",
            "cash_balance_base",
            "position_market_value_base",
            "open_cost_basis_base",
            "realized_pnl",
            "unrealized_pnl",
            "unrealized_pnl_change",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "pending_settlement_currency_gains",
            "instrument_currency_gains",
            GROUP_CAPITAL_FLOW_IN_FIELD,
            GROUP_CAPITAL_FLOW_OUT_FIELD,
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(base_slice.get(field_name))
            if value is None:
                grouped_slice[field_name] = None
                continue
            current_value = grouped_slice.get(field_name)
            if current_value is None:
                continue
            grouped_slice[field_name] = (_safe_float(current_value) or 0.0) + value

    grouped_slices = sorted(
        grouped.values(),
        key=lambda item: (item["as_of_date"], item["group_key"]),
    )
    for grouped_slice in grouped_slices:
        slice_key = (grouped_slice["as_of_date"], grouped_slice["group_key"])
        grouped_slice["coverage_state"] = merge_group_coverage_state(
            coverage_states_by_group[slice_key]
        )
        total_pnl = _safe_float(grouped_slice.get("total_pnl"))
        beginning_value_base = _safe_float(grouped_slice.get("beginning_value_base"))
        ending_value_base = _safe_float(grouped_slice.get("ending_value_base"))
        capital_flow_in_base = _safe_float(
            grouped_slice.get(GROUP_CAPITAL_FLOW_IN_FIELD)
        )
        capital_flow_out_base = _safe_float(
            grouped_slice.get(GROUP_CAPITAL_FLOW_OUT_FIELD)
        )
        grouped_slice["daily_return"] = daily_group_return_from_components(
            beginning_value_base=beginning_value_base,
            ending_value_base=ending_value_base,
            total_pnl=total_pnl,
            capital_flow_in_base=capital_flow_in_base,
            capital_flow_out_base=capital_flow_out_base,
        )
        grouped_slice["return_observation_eligible"] = (
            grouped_slice["daily_return"] is not None
            and grouped_slice["coverage_state"] == "complete"
            and (
                int(grouped_slice.get("market_observation_count") or 0) > 0
                or abs(float(grouped_slice["daily_return"])) > 1e-12
            )
        )
    return grouped_slices


def build_taxonomy_contribution_report(
    *,
    taxonomy: dict[str, object],
    base_report: dict[str, object],
    grouped_daily_slices: list[dict[str, object]],
) -> dict[str, object]:
    base_summary = (
        deepcopy(base_report.get("summary"))
        if isinstance(base_report.get("summary"), dict)
        else {}
    )
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    resolved_start_date = _parse_iso_date(base_summary.get("start_date"))
    resolved_end_date = _parse_iso_date(base_summary.get("end_date"))

    if resolved_start_date is None or resolved_end_date is None:
        return {
            "portfolio_id": base_report["portfolio_id"],
            "base_currency": base_report["base_currency"],
            "valuation_timezone": base_report["valuation_timezone"],
            "valuation_cutoff_policy": base_report["valuation_cutoff_policy"],
            "summary": {
                **base_summary,
                "axis": "taxonomy",
                "taxonomy_id": taxonomy_id,
                "slice_count": 0,
                "group_count": 0,
                "total_period_contribution": None,
                "contribution_residual": None,
            },
            "lines": [],
            "daily_slices": [],
            "_portfolio_daily_series": [],
        }

    grouped_slices = sorted(
        grouped_daily_slices,
        key=lambda item: (item["as_of_date"], item["group_key"]),
    )
    available_beginning_weight_dates = {
        item["as_of_date"]
        for item in grouped_slices
        if isinstance(item.get("as_of_date"), date)
        and _safe_float(item.get("beginning_weight")) is not None
    }

    line_accumulators: dict[str, dict[str, object]] = {}
    start_values: dict[str, float | None] = {}
    beginning_weights: dict[str, float | None] = {}
    end_values: dict[str, float | None] = {}
    ending_weights: dict[str, float | None] = {}
    first_slice_dates: dict[str, date] = {}

    for grouped_slice in grouped_slices:
        group_key = str(grouped_slice.get("group_key") or "")
        as_of_date = grouped_slice.get("as_of_date")
        if not group_key or not isinstance(as_of_date, date):
            continue

        if group_key not in first_slice_dates or as_of_date < first_slice_dates[group_key]:
            start_values[group_key] = _safe_float(
                grouped_slice.get("beginning_value_base")
            )
            beginning_weights[group_key] = _safe_float(
                grouped_slice.get("beginning_weight")
            )
            first_slice_dates[group_key] = as_of_date
        if as_of_date == resolved_end_date:
            end_values[group_key] = _safe_float(grouped_slice.get("ending_value_base"))
            ending_weights[group_key] = _safe_float(grouped_slice.get("ending_weight"))

        accumulator = line_accumulators.setdefault(
            group_key,
            {
                "axis": "taxonomy",
                "group_key": group_key,
                "group_label": str(grouped_slice.get("group_label") or group_key),
                "start_value_base": 0.0,
                "end_value_base": 0.0,
                "beginning_weight": 0.0,
                "average_weight": 0.0,
                "ending_weight": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "pending_settlement_currency_gains": 0.0,
                "instrument_currency_gains": 0.0,
                "total_pnl": 0.0,
                "period_contribution": 0.0,
            },
        )
        beginning_weight = _safe_float(grouped_slice.get("beginning_weight"))
        if beginning_weight is not None:
            accumulator["average_weight"] = (
                (_safe_float(accumulator.get("average_weight")) or 0.0)
                + beginning_weight
            )
        for field_name in (
            "realized_pnl",
            "unrealized_pnl_change",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "pending_settlement_currency_gains",
            "instrument_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(grouped_slice.get(field_name))
            target_field = (
                "period_contribution"
                if field_name == "daily_contribution"
                else field_name
            )
            if value is None:
                if field_name == "pending_settlement_currency_gains":
                    accumulator[target_field] = None
                    accumulator["total_pnl"] = None
                    accumulator["period_contribution"] = None
                continue
            if accumulator.get(target_field) is None:
                continue
            accumulator[target_field] = (
                (_safe_float(accumulator.get(target_field)) or 0.0) + value
            )

    lines: list[dict[str, object]] = []
    weight_denominator = len(available_beginning_weight_dates)
    end_weight_available = _safe_float(base_summary.get("end_nav")) is not None
    start_value_available = _safe_float(base_summary.get("start_nav")) is not None
    end_value_available = _safe_float(base_summary.get("end_nav")) is not None

    for group_key, accumulator in line_accumulators.items():
        accumulator["start_value_base"] = (
            start_values.get(group_key, 0.0) if start_value_available else None
        )
        accumulator["end_value_base"] = (
            end_values.get(group_key, 0.0) if end_value_available else None
        )
        accumulator["beginning_weight"] = (
            beginning_weights.get(group_key, 0.0) if start_value_available else None
        )
        accumulator["ending_weight"] = (
            ending_weights.get(group_key, 0.0) if end_weight_available else None
        )
        accumulator["average_weight"] = (
            (_safe_float(accumulator.get("average_weight")) or 0.0)
            / weight_denominator
            if weight_denominator > 0
            else None
        )
        lines.append(accumulator)

    lines.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("period_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )

    total_period_contribution = sum(
        (_safe_float(item.get("period_contribution")) or 0.0) for item in lines
    )
    portfolio_arithmetic_return = _safe_float(
        base_summary.get("portfolio_arithmetic_return")
    )
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None
        else None
    )
    coverage_state = str(base_summary.get("coverage_state") or "unavailable")
    if coverage_state == "complete" and any(
        str(item.get("coverage_state") or "unavailable") != "complete"
        for item in grouped_slices
    ):
        coverage_state = "partial"

    return {
        "portfolio_id": base_report["portfolio_id"],
        "base_currency": base_report["base_currency"],
        "valuation_timezone": base_report["valuation_timezone"],
        "valuation_cutoff_policy": base_report["valuation_cutoff_policy"],
        "summary": {
            **base_summary,
            "axis": "taxonomy",
            "taxonomy_id": taxonomy_id,
            "coverage_state": coverage_state,
            "slice_count": len(grouped_slices),
            "group_count": len(lines),
            "total_period_contribution": total_period_contribution,
            "contribution_residual": contribution_residual,
        },
        "lines": lines,
        "daily_slices": grouped_slices,
        "_portfolio_daily_series": list(base_report.get("_portfolio_daily_series") or []),
    }


def filter_contribution_report_by_group_key(
    report: dict[str, object],
    *,
    group_key: str | None,
) -> dict[str, object]:
    resolved_group_key = str(group_key or "").strip()
    if not resolved_group_key:
        return report

    filtered_report = deepcopy(report)
    summary = (
        filtered_report.get("summary")
        if isinstance(filtered_report.get("summary"), dict)
        else {}
    )
    lines = [
        item
        for item in list(filtered_report.get("lines") or [])
        if str(item.get("group_key") or "") == resolved_group_key
    ]
    daily_slices = [
        item
        for item in list(filtered_report.get("daily_slices") or [])
        if str(item.get("group_key") or "") == resolved_group_key
    ]
    group_label = None
    if lines:
        group_label = str(lines[0].get("group_label") or resolved_group_key)
    elif daily_slices:
        group_label = str(daily_slices[0].get("group_label") or resolved_group_key)

    filtered_report["lines"] = lines
    filtered_report["daily_slices"] = daily_slices

    total_period_contribution = sum(
        (_safe_float(item.get("period_contribution")) or 0.0) for item in lines
    )
    portfolio_arithmetic_return = _safe_float(
        summary.get("portfolio_arithmetic_return")
    )
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None
        else None
    )
    observation_dates = {
        as_of_date
        for item in daily_slices
        if isinstance((as_of_date := item.get("as_of_date")), date)
        and _safe_float(item.get("daily_contribution")) is not None
    }
    coverage_state = merge_group_coverage_state(
        [str(item.get("coverage_state") or "unavailable") for item in daily_slices]
    )
    if not daily_slices:
        coverage_state = "unavailable"

    summary["group_key"] = resolved_group_key
    summary["group_label"] = group_label
    summary["coverage_state"] = coverage_state
    summary["slice_count"] = len(daily_slices)
    summary["group_count"] = len(
        {str(item.get("group_key") or "") for item in lines}
    )
    summary["observation_count"] = len(observation_dates)
    summary["total_period_contribution"] = total_period_contribution
    summary["contribution_residual"] = contribution_residual
    return filtered_report


def build_contribution_report_from_daily_slices_core(
    *,
    portfolio_id: str,
    base_currency: str,
    valuation_timezone: str,
    valuation_cutoff_policy: str,
    snapshots: list[dict[str, object]],
    daily_slices: list[dict[str, object]],
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    group_key: str | None = None,
    start_is_close_boundary: bool = False,
) -> dict[str, object]:
    normalized_snapshots: list[dict[str, object]] = []
    for snapshot in snapshots:
        normalized_snapshot = dict(snapshot)
        snapshot_date = _parse_iso_date(normalized_snapshot.get("as_of_date"))
        if snapshot_date is not None:
            normalized_snapshot["as_of_date"] = snapshot_date
            normalized_snapshots.append(normalized_snapshot)

    normalized_slices: list[dict[str, object]] = []
    for daily_slice in daily_slices:
        normalized_slice = dict(daily_slice)
        slice_date = _parse_iso_date(normalized_slice.get("as_of_date"))
        if slice_date is not None:
            normalized_slice["as_of_date"] = slice_date
            normalized_slices.append(normalized_slice)

    available_dates = [
        item["as_of_date"]
        for item in [*normalized_snapshots, *normalized_slices]
        if isinstance(item.get("as_of_date"), date)
    ]
    resolved_start_date = start_date or (min(available_dates) if available_dates else None)
    resolved_end_date = end_date or (max(available_dates) if available_dates else None)
    if (
        resolved_start_date is None
        or resolved_end_date is None
        or resolved_end_date < resolved_start_date
    ):
        return {
            "portfolio_id": portfolio_id,
            "base_currency": base_currency,
            "valuation_timezone": valuation_timezone,
            "valuation_cutoff_policy": valuation_cutoff_policy,
            "summary": {
                "axis": axis,
                "group_key": str(group_key or "").strip() or None,
                "group_label": None,
                "start_date": start_date,
                "end_date": end_date,
                "coverage_state": "unavailable",
                "slice_count": 0,
                "group_count": 0,
                "observation_count": 0,
                "start_nav": None,
                "end_nav": None,
                "portfolio_arithmetic_return": None,
                "portfolio_cumulative_twr": None,
                "total_period_contribution": None,
                "contribution_residual": None,
            },
            "lines": [],
            "daily_slices": [],
            "_portfolio_daily_series": [],
        }

    start_snapshot = next(
        (
            snapshot
            for snapshot in normalized_snapshots
            if snapshot.get("as_of_date") == resolved_start_date
        ),
        None,
    )
    starts_on_valuation_anchor = bool(
        start_is_close_boundary
        or any(
            daily_slice.get("as_of_date") == resolved_start_date
            and bool(daily_slice.get("_is_initial_valuation_anchor"))
            for daily_slice in normalized_slices
        )
        or (
            start_snapshot is not None
            and _safe_float(start_snapshot.get("nav")) is not None
            and _safe_float(start_snapshot.get("daily_twr")) is None
            and bool(start_snapshot.get("return_chain_continuous"))
        )
    )
    if starts_on_valuation_anchor:
        normalized_snapshots = [
            (
                _snapshot_as_close_boundary(snapshot)
                if snapshot.get("as_of_date") == resolved_start_date
                else snapshot
            )
            for snapshot in normalized_snapshots
        ]
        normalized_slices = [
            (
                _slice_as_close_boundary(daily_slice)
                if daily_slice.get("as_of_date") == resolved_start_date
                else daily_slice
            )
            for daily_slice in normalized_slices
        ]

    snapshots_by_date = {
        snapshot["as_of_date"]: snapshot
        for snapshot in normalized_snapshots
        if isinstance(snapshot.get("as_of_date"), date)
    }
    in_period_slices = [
        daily_slice
        for daily_slice in normalized_slices
        if isinstance(daily_slice.get("as_of_date"), date)
        and resolved_start_date <= daily_slice["as_of_date"] <= resolved_end_date
    ]
    weight_observation_dates = {
        cast(date, daily_slice["as_of_date"])
        for daily_slice in in_period_slices
        if isinstance(daily_slice.get("as_of_date"), date)
        and not bool(daily_slice.get("_is_initial_valuation_anchor"))
        and _safe_float(daily_slice.get("beginning_weight")) is not None
    }
    weight_observation_count = len(weight_observation_dates)
    first_period_slices = [
        daily_slice
        for daily_slice in in_period_slices
        if daily_slice.get("as_of_date") == resolved_start_date
    ]
    starts_on_initial_valuation_anchor = any(
        bool(daily_slice.get("_is_initial_valuation_anchor"))
        for daily_slice in first_period_slices
    )

    contribution_growth_index = 1.0
    has_return_observation = False
    arithmetic_return = 0.0
    observation_count = 0
    for as_of_date in _iter_dates(resolved_start_date, resolved_end_date):
        snapshot = snapshots_by_date.get(as_of_date)
        daily_twr = _safe_float((snapshot or {}).get("daily_twr"))
        if daily_twr is not None and isfinite(daily_twr):
            arithmetic_return += daily_twr
            contribution_growth_index *= 1.0 + daily_twr
            has_return_observation = True
            observation_count += 1

    line_accumulators: dict[str, dict[str, object]] = {}
    line_first_slice_dates: dict[str, date] = {}
    for daily_slice in in_period_slices:
        line_group_key = str(daily_slice.get("group_key") or "")
        as_of_date = daily_slice.get("as_of_date")
        if not isinstance(as_of_date, date):
            continue
        accumulator = line_accumulators.setdefault(
            line_group_key,
            {
                "axis": axis,
                "group_key": line_group_key,
                "group_label": str(daily_slice.get("group_label") or line_group_key),
                "start_value_base": _period_initial_slice_value(
                    daily_slice,
                    value_field="beginning_value_base",
                    anchor_value_field="ending_value_base",
                ),
                "end_value_base": daily_slice.get("ending_value_base"),
                "beginning_weight": _period_initial_slice_value(
                    daily_slice,
                    value_field="beginning_weight",
                    anchor_value_field="ending_weight",
                ),
                "average_weight": 0.0,
                "ending_weight": daily_slice.get("ending_weight"),
                "realized_pnl": 0.0,
                "unrealized_pnl_change": 0.0,
                "income_cash_amount": 0.0,
                "expense_cash_amount": 0.0,
                "fee_amount": 0.0,
                "tax_amount": 0.0,
                "cash_currency_gains": 0.0,
                "pending_settlement_currency_gains": 0.0,
                "instrument_currency_gains": 0.0,
                "total_pnl": 0.0,
                "period_contribution": 0.0,
            },
        )
        if (
            line_group_key not in line_first_slice_dates
            or as_of_date < line_first_slice_dates[line_group_key]
        ):
            accumulator["start_value_base"] = _period_initial_slice_value(
                daily_slice,
                value_field="beginning_value_base",
                anchor_value_field="ending_value_base",
            )
            accumulator["beginning_weight"] = _period_initial_slice_value(
                daily_slice,
                value_field="beginning_weight",
                anchor_value_field="ending_weight",
            )
            line_first_slice_dates[line_group_key] = as_of_date
        accumulator["end_value_base"] = daily_slice.get("ending_value_base")
        accumulator["ending_weight"] = daily_slice.get("ending_weight")
        beginning_weight = _safe_float(daily_slice.get("beginning_weight"))
        if (
            beginning_weight is not None
            and not bool(daily_slice.get("_is_initial_valuation_anchor"))
        ):
            accumulator["average_weight"] = (
                (_safe_float(accumulator.get("average_weight")) or 0.0)
                + beginning_weight
            )
        for field_name in (
            "realized_pnl",
            "unrealized_pnl_change",
            "income_cash_amount",
            "expense_cash_amount",
            "fee_amount",
            "tax_amount",
            "cash_currency_gains",
            "pending_settlement_currency_gains",
            "instrument_currency_gains",
            "total_pnl",
            "daily_contribution",
        ):
            value = _safe_float(daily_slice.get(field_name))
            target_field = (
                "period_contribution"
                if field_name == "daily_contribution"
                else field_name
            )
            if value is None:
                if field_name == "pending_settlement_currency_gains":
                    accumulator[target_field] = None
                    accumulator["total_pnl"] = None
                    accumulator["period_contribution"] = None
                continue
            if accumulator.get(target_field) is None:
                continue
            accumulator[target_field] = (
                (_safe_float(accumulator.get(target_field)) or 0.0) + value
            )

    lines: list[dict[str, object]] = []
    for accumulator in line_accumulators.values():
        average_weight_total = _safe_float(accumulator.get("average_weight")) or 0.0
        accumulator["average_weight"] = (
            average_weight_total / weight_observation_count
            if weight_observation_count > 0
            else None
        )
        lines.append(accumulator)
    lines.sort(
        key=lambda item: (
            -abs(_safe_float(item.get("period_contribution")) or 0.0),
            str(item.get("group_key") or ""),
        )
    )

    in_period_snapshots = [
        snapshots_by_date[as_of_date]
        for as_of_date in _iter_dates(resolved_start_date, resolved_end_date)
        if as_of_date in snapshots_by_date
    ]
    portfolio_daily_series = [
        {
            "as_of_date": snapshot["as_of_date"],
            "daily_twr": _safe_float(snapshot.get("daily_twr")),
            "return_observation_eligible": bool(
                snapshot.get("return_observation_eligible")
            ),
            "market_observation_count": int(
                snapshot.get("market_observation_count") or 0
            ),
            "coverage_state": str(snapshot.get("coverage_state") or "unavailable"),
        }
        for snapshot in in_period_snapshots
        if isinstance(snapshot.get("as_of_date"), date)
    ]
    coverage_state = "unavailable"
    if in_period_snapshots:
        coverage_state = "complete"
        if any(
            str(snapshot.get("coverage_state") or "unavailable") != "complete"
            for snapshot in in_period_snapshots
        ):
            coverage_state = "partial"
        if any(
            (
                _safe_float(item.get("ending_value_base")) is None
                if bool(item.get("_is_initial_valuation_anchor"))
                else str(item.get("coverage_state") or "unavailable")
                != "complete"
            )
            for item in in_period_slices
        ):
            coverage_state = "partial"

    total_period_contribution = sum(
        (_safe_float(line.get("period_contribution")) or 0.0) for line in lines
    )
    zero_length_close_interval = bool(
        starts_on_initial_valuation_anchor
        and resolved_start_date == resolved_end_date
        and in_period_snapshots
        and coverage_state == "complete"
    )
    portfolio_arithmetic_return = (
        arithmetic_return
        if observation_count > 0
        else (0.0 if zero_length_close_interval else None)
    )
    portfolio_cumulative_twr = (
        contribution_growth_index - 1.0
        if has_return_observation
        else (0.0 if zero_length_close_interval else None)
    )
    contribution_residual = (
        portfolio_arithmetic_return - total_period_contribution
        if portfolio_arithmetic_return is not None
        else None
    )

    report = {
        "portfolio_id": portfolio_id,
        "base_currency": base_currency,
        "valuation_timezone": valuation_timezone,
        "valuation_cutoff_policy": valuation_cutoff_policy,
        "summary": {
            "axis": axis,
            "group_key": None,
            "group_label": None,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "coverage_state": coverage_state,
            "slice_count": len(in_period_slices),
            "group_count": len(lines),
            "observation_count": observation_count,
            "start_nav": _safe_float(
                (in_period_snapshots[0] if in_period_snapshots else {}).get(
                    (
                        "ending_nav"
                        if starts_on_initial_valuation_anchor
                        else "beginning_nav"
                    )
                )
            ),
            "end_nav": _safe_float(
                (in_period_snapshots[-1] if in_period_snapshots else {}).get(
                    "ending_nav"
                )
            ),
            "portfolio_arithmetic_return": portfolio_arithmetic_return,
            "portfolio_cumulative_twr": portfolio_cumulative_twr,
            "total_period_contribution": total_period_contribution,
            "contribution_residual": contribution_residual,
        },
        "lines": lines,
        "daily_slices": in_period_slices,
        "_portfolio_daily_series": portfolio_daily_series,
    }
    return filter_contribution_report_by_group_key(report, group_key=group_key)


def merge_calculation_detail_daily_slices(
    daily_slices: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[date, str], dict[str, object]] = {}
    coverage_states_by_group: dict[tuple[date, str], list[str]] = defaultdict(list)

    for daily_slice in daily_slices:
        as_of_date = daily_slice.get("as_of_date")
        group_key = str(daily_slice.get("group_key") or "")
        if not isinstance(as_of_date, date) or not group_key:
            continue
        slice_key = (as_of_date, group_key)
        grouped_slice = grouped.setdefault(
            slice_key,
            {
                "as_of_date": as_of_date,
                "axis": str(daily_slice.get("axis") or ""),
                "group_key": group_key,
                "group_label": str(daily_slice.get("group_label") or group_key),
                "coverage_state": "complete",
                "market_observation_count": int(
                    daily_slice.get("market_observation_count") or 0
                ),
                "return_observation_eligible": bool(
                    daily_slice.get("return_observation_eligible")
                ),
                "_is_initial_valuation_anchor": bool(
                    daily_slice.get("_is_initial_valuation_anchor")
                ),
                "daily_return": None,
                **{
                    field_name: 0.0
                    for field_name in _CALCULATION_DETAIL_ADDITIVE_SLICE_FIELDS
                },
            },
        )
        coverage_states_by_group[slice_key].append(
            str(daily_slice.get("coverage_state") or "unavailable")
        )
        grouped_slice["market_observation_count"] = max(
            int(grouped_slice.get("market_observation_count") or 0),
            int(daily_slice.get("market_observation_count") or 0),
        )
        grouped_slice["_is_initial_valuation_anchor"] = bool(
            grouped_slice.get("_is_initial_valuation_anchor")
            or daily_slice.get("_is_initial_valuation_anchor")
        )
        for field_name in _CALCULATION_DETAIL_ADDITIVE_SLICE_FIELDS:
            value = _safe_float(daily_slice.get(field_name))
            if value is None:
                grouped_slice[field_name] = None
                continue
            current_value = grouped_slice.get(field_name)
            if current_value is None:
                continue
            grouped_slice[field_name] = (_safe_float(current_value) or 0.0) + value

    grouped_slices = sorted(
        grouped.values(), key=lambda item: (item["as_of_date"], item["group_key"])
    )
    for grouped_slice in grouped_slices:
        slice_key = (grouped_slice["as_of_date"], grouped_slice["group_key"])
        grouped_slice["coverage_state"] = merge_group_coverage_state(
            coverage_states_by_group[slice_key]
        )
        grouped_slice["daily_return"] = daily_group_return_from_components(
            beginning_value_base=_safe_float(
                grouped_slice.get("beginning_value_base")
            ),
            ending_value_base=_safe_float(grouped_slice.get("ending_value_base")),
            total_pnl=_safe_float(grouped_slice.get("total_pnl")),
            capital_flow_in_base=_safe_float(
                grouped_slice.get(GROUP_CAPITAL_FLOW_IN_FIELD)
            ),
            capital_flow_out_base=_safe_float(
                grouped_slice.get(GROUP_CAPITAL_FLOW_OUT_FIELD)
            ),
        )
        grouped_slice["return_observation_eligible"] = (
            grouped_slice["daily_return"] is not None
            and grouped_slice["coverage_state"] == "complete"
            and (
                int(grouped_slice.get("market_observation_count") or 0) > 0
                or abs(float(grouped_slice["daily_return"])) > 1e-12
            )
        )
    return grouped_slices


def risk_metric_defaults(
    calculation_frequency: CalculationFrequency,
) -> dict[str, object]:
    return {
        "risk_calculation_frequency": calculation_frequency,
        "risk_return_observation_count": 0,
        "risk_annualization_periods_per_year": None,
        "annualized_volatility": None,
        "sharpe_ratio": None,
        "correlation_to_portfolio": None,
        "beta_to_portfolio": None,
        "realized_risk_contribution": None,
    }


def sample_covariance(
    left_values: list[float], right_values: list[float]
) -> float | None:
    if len(left_values) < 2 or len(left_values) != len(right_values):
        return None
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    return sum(
        (left_value - left_mean) * (right_values[index] - right_mean)
        for index, left_value in enumerate(left_values)
    ) / (len(left_values) - 1)


def sample_correlation(
    left_values: list[float], right_values: list[float]
) -> float | None:
    covariance = sample_covariance(left_values, right_values)
    left_stddev = period_metrics.sample_stddev(left_values)
    right_stddev = period_metrics.sample_stddev(right_values)
    if (
        covariance is None
        or left_stddev is None
        or right_stddev is None
        or left_stddev <= 1e-12
        or right_stddev <= 1e-12
    ):
        return None
    return covariance / (left_stddev * right_stddev)


def compound_returns(values: list[float]) -> float | None:
    return prod(1.0 + value for value in values) - 1.0 if values else None


def bucketed_portfolio_returns(
    portfolio_daily_series: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    final_date: date | None,
) -> dict[date, float]:
    bucket_returns: dict[date, list[float]] = defaultdict(list)
    for point in sorted(
        portfolio_daily_series, key=lambda item: str(item.get("as_of_date") or "")
    ):
        as_of_date = _parse_iso_date(point.get("as_of_date"))
        daily_return = _safe_float(point.get("daily_twr"))
        if as_of_date is None or daily_return is None or not isfinite(daily_return):
            continue
        if not bool(point.get("return_observation_eligible")):
            continue
        bucket_date = period_end_date(
            as_of_date, calculation_frequency, final_date=final_date
        )
        bucket_returns[bucket_date].append(daily_return)
    return {
        bucket_date: bucket_return
        for bucket_date, values in bucket_returns.items()
        if (bucket_return := compound_returns(values)) is not None
    }


def bucketed_group_risk_inputs(
    daily_slices: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    final_date: date | None,
) -> dict[str, dict[date, dict[str, object]]]:
    grouped: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for daily_slice in sorted(
        daily_slices, key=lambda item: str(item.get("as_of_date") or "")
    ):
        group_key = str(daily_slice.get("group_key") or "")
        as_of_date = _parse_iso_date(daily_slice.get("as_of_date"))
        if not group_key or as_of_date is None:
            continue
        if not bool(daily_slice.get("return_observation_eligible")):
            continue
        bucket_date = period_end_date(
            as_of_date, calculation_frequency, final_date=final_date
        )
        bucket = grouped[group_key].setdefault(
            bucket_date,
            {
                "returns": [],
                "contribution": 0.0,
                "has_contribution": False,
            },
        )
        daily_return = _safe_float(daily_slice.get("daily_return"))
        if daily_return is not None and isfinite(daily_return):
            bucket_returns = bucket.get("returns")
            if isinstance(bucket_returns, list):
                bucket_returns.append(daily_return)
        daily_contribution = _safe_float(daily_slice.get("daily_contribution"))
        if daily_contribution is not None and isfinite(daily_contribution):
            bucket["contribution"] = (
                _safe_float(bucket.get("contribution")) or 0.0
            ) + daily_contribution
            bucket["has_contribution"] = True
    return grouped


def bucketed_realized_contribution_matrix(
    daily_slices: list[dict[str, object]],
    portfolio_daily_series: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    final_date: date | None,
) -> tuple[list[date], list[float], dict[str, list[float]]]:
    portfolio_observations_by_bucket: dict[
        date, list[tuple[date, float]]
    ] = defaultdict(list)
    eligible_portfolio_dates: set[date] = set()
    for point in sorted(
        portfolio_daily_series, key=lambda item: str(item.get("as_of_date") or "")
    ):
        as_of_date = _parse_iso_date(point.get("as_of_date"))
        daily_return = _safe_float(point.get("daily_twr"))
        if as_of_date is None or daily_return is None or not isfinite(daily_return):
            continue
        if not bool(point.get("return_observation_eligible")):
            continue
        eligible_portfolio_dates.add(as_of_date)
        bucket_date = period_end_date(
            as_of_date, calculation_frequency, final_date=final_date
        )
        portfolio_observations_by_bucket[bucket_date].append(
            (as_of_date, daily_return)
        )

    group_daily_contributions: dict[str, dict[date, float]] = defaultdict(dict)
    invalid_bucket_dates: set[date] = set()
    for daily_slice in daily_slices:
        group_key = str(daily_slice.get("group_key") or "")
        as_of_date = _parse_iso_date(daily_slice.get("as_of_date"))
        if (
            not group_key
            or as_of_date is None
            or as_of_date not in eligible_portfolio_dates
        ):
            continue
        bucket_date = period_end_date(
            as_of_date, calculation_frequency, final_date=final_date
        )
        daily_contribution = _safe_float(daily_slice.get("daily_contribution"))
        if daily_contribution is None or not isfinite(daily_contribution):
            invalid_bucket_dates.add(bucket_date)
            continue
        group_daily_contributions[group_key][as_of_date] = (
            group_daily_contributions[group_key].get(as_of_date, 0.0)
            + daily_contribution
        )

    bucket_dates: list[date] = []
    portfolio_bucket_returns: list[float] = []
    group_bucket_contributions: dict[str, list[float]] = {
        group_key: [] for group_key in sorted(group_daily_contributions)
    }
    for bucket_date in sorted(portfolio_observations_by_bucket):
        if bucket_date in invalid_bucket_dates:
            continue
        observations = sorted(
            portfolio_observations_by_bucket[bucket_date], key=lambda item: item[0]
        )
        portfolio_bucket_return = compound_returns(
            [value for _as_of_date, value in observations]
        )
        if portfolio_bucket_return is None:
            continue

        trailing_growth_by_date: dict[date, float] = {}
        trailing_growth = 1.0
        for as_of_date, daily_return in reversed(observations):
            trailing_growth_by_date[as_of_date] = trailing_growth
            trailing_growth *= 1.0 + daily_return

        bucket_dates.append(bucket_date)
        portfolio_bucket_returns.append(portfolio_bucket_return)
        for group_key, contributions_by_date in group_daily_contributions.items():
            bucket_contribution = sum(
                contributions_by_date.get(as_of_date, 0.0)
                * trailing_growth_by_date[as_of_date]
                for as_of_date, _daily_return in observations
            )
            group_bucket_contributions[group_key].append(bucket_contribution)

    return bucket_dates, portfolio_bucket_returns, group_bucket_contributions


def _daily_slice_has_risk_exposure(daily_slice: dict[str, object]) -> bool:
    return any(
        value is not None and abs(value) > 1e-12
        for value in (
            _safe_float(daily_slice.get("beginning_value_base")),
            _safe_float(daily_slice.get("ending_value_base")),
            _safe_float(daily_slice.get(GROUP_CAPITAL_FLOW_IN_FIELD)),
            _safe_float(daily_slice.get(GROUP_CAPITAL_FLOW_OUT_FIELD)),
        )
    )


def _group_risk_elapsed_boundaries(
    daily_slices: list[dict[str, object]],
    *,
    portfolio_start_boundary_date: date | None,
    portfolio_end_date: date | None,
) -> dict[str, tuple[date, date]]:
    """Return exposure-specific EOD boundaries for group risk annualization."""

    rows_by_group: dict[str, list[tuple[date, dict[str, object]]]] = defaultdict(
        list
    )
    for daily_slice in daily_slices:
        group_key = str(daily_slice.get("group_key") or "")
        slice_date = _parse_iso_date(daily_slice.get("as_of_date"))
        if (
            not group_key
            or slice_date is None
            or not _daily_slice_has_risk_exposure(daily_slice)
        ):
            continue
        rows_by_group[group_key].append((slice_date, daily_slice))

    boundaries: dict[str, tuple[date, date]] = {}
    for group_key, dated_rows in rows_by_group.items():
        dated_rows.sort(key=lambda item: item[0])
        first_date, first_row = dated_rows[0]
        last_date = dated_rows[-1][0]
        group_start_boundary = (
            first_date
            if bool(first_row.get("_is_initial_valuation_anchor"))
            else first_date - timedelta(days=1)
        )
        if (
            portfolio_start_boundary_date is not None
            and group_start_boundary < portfolio_start_boundary_date
        ):
            group_start_boundary = portfolio_start_boundary_date
        group_end_boundary = (
            min(last_date, portfolio_end_date)
            if portfolio_end_date is not None
            else last_date
        )
        if group_end_boundary > group_start_boundary:
            boundaries[group_key] = (
                group_start_boundary,
                group_end_boundary,
            )
    return boundaries


def realized_risk_attribution_by_group(
    daily_slices: list[dict[str, object]],
    portfolio_daily_series: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    start_date: date | None = None,
    final_date: date | None,
) -> dict[str, dict[str, object]]:
    portfolio_returns = bucketed_portfolio_returns(
        portfolio_daily_series,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    grouped_inputs = bucketed_group_risk_inputs(
        daily_slices,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    elapsed_boundaries_by_group = _group_risk_elapsed_boundaries(
        daily_slices,
        portfolio_start_boundary_date=start_date,
        portfolio_end_date=final_date,
    )
    risk_by_group: dict[str, dict[str, object]] = {}
    for group_key, buckets in grouped_inputs.items():
        own_returns_by_date: dict[date, float] = {}
        for bucket_date in sorted(buckets):
            bucket = buckets[bucket_date]
            bucket_returns = [
                value
                for value in list(bucket.get("returns") or [])
                if isinstance(value, (int, float)) and isfinite(float(value))
            ]
            own_return = compound_returns([float(value) for value in bucket_returns])
            if own_return is not None:
                own_returns_by_date[bucket_date] = own_return

        own_dates = sorted(own_returns_by_date)
        own_values = [own_returns_by_date[item] for item in own_dates]
        group_elapsed_boundaries = elapsed_boundaries_by_group.get(group_key)
        periods_per_year = (
            period_metrics.periods_per_year_from_observations(
                observation_count=len(own_values),
                start_date=group_elapsed_boundaries[0],
                end_date=group_elapsed_boundaries[1],
            )
            if group_elapsed_boundaries is not None
            else period_metrics.annualization_periods_per_year_from_dates(
                own_dates,
                observation_count=len(own_values),
            )
        )
        volatility = period_metrics.sample_stddev(own_values)
        annualized_volatility = (
            volatility * sqrt(periods_per_year)
            if volatility is not None and periods_per_year is not None
            else None
        )
        mean_return = sum(own_values) / len(own_values) if own_values else None
        annualized_mean_return = (
            mean_return * periods_per_year
            if mean_return is not None and periods_per_year is not None
            else None
        )
        sharpe_ratio = (
            annualized_mean_return / annualized_volatility
            if annualized_mean_return is not None
            and annualized_volatility is not None
            and annualized_volatility > 1e-12
            else None
        )

        own_pair_dates = sorted(
            bucket_date
            for bucket_date in own_returns_by_date
            if bucket_date in portfolio_returns
        )
        own_pair_values = [own_returns_by_date[item] for item in own_pair_dates]
        portfolio_returns_for_own = [portfolio_returns[item] for item in own_pair_dates]
        own_pair_covariance = sample_covariance(
            own_pair_values, portfolio_returns_for_own
        )
        own_pair_portfolio_variance = sample_covariance(
            portfolio_returns_for_own, portfolio_returns_for_own
        )

        risk_by_group[group_key] = {
            "risk_calculation_frequency": calculation_frequency,
            "risk_return_observation_count": len(own_pair_dates),
            "risk_annualization_periods_per_year": periods_per_year,
            "annualized_volatility": annualized_volatility,
            "sharpe_ratio": sharpe_ratio,
            "correlation_to_portfolio": sample_correlation(
                own_pair_values, portfolio_returns_for_own
            ),
            "beta_to_portfolio": (
                own_pair_covariance / own_pair_portfolio_variance
                if own_pair_covariance is not None
                and own_pair_portfolio_variance is not None
                and own_pair_portfolio_variance > 1e-12
                else None
            ),
            "realized_risk_contribution": None,
        }
    (
        _common_dates,
        common_portfolio_returns,
        common_group_contributions,
    ) = bucketed_realized_contribution_matrix(
        daily_slices,
        portfolio_daily_series,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    common_portfolio_variance = sample_covariance(
        common_portfolio_returns, common_portfolio_returns
    )
    for group_key, contribution_values in common_group_contributions.items():
        group_metrics = risk_by_group.setdefault(
            group_key, risk_metric_defaults(calculation_frequency)
        )
        contribution_covariance = sample_covariance(
            contribution_values, common_portfolio_returns
        )
        group_metrics["realized_risk_contribution"] = (
            contribution_covariance / common_portfolio_variance
            if contribution_covariance is not None
            and common_portfolio_variance is not None
            and common_portfolio_variance > 1e-12
            else None
        )
    return risk_by_group


def portfolio_realized_risk_summary(
    portfolio_daily_series: list[dict[str, object]],
    *,
    calculation_frequency: CalculationFrequency,
    start_date: date | None = None,
    final_date: date | None,
) -> dict[str, object]:
    portfolio_returns_by_date = bucketed_portfolio_returns(
        portfolio_daily_series,
        calculation_frequency=calculation_frequency,
        final_date=final_date,
    )
    return_dates = sorted(portfolio_returns_by_date)
    returns = [portfolio_returns_by_date[item] for item in return_dates]
    periods_per_year = (
        period_metrics.periods_per_year_from_observations(
            observation_count=len(returns),
            start_date=start_date,
            end_date=final_date,
        )
        if start_date is not None and final_date is not None
        else period_metrics.annualization_periods_per_year_from_dates(
            return_dates,
            observation_count=len(returns),
        )
    )
    volatility = period_metrics.sample_stddev(returns)
    annualized_volatility = (
        volatility * sqrt(periods_per_year)
        if volatility is not None and periods_per_year is not None
        else None
    )
    mean_return = sum(returns) / len(returns) if returns else None
    annualized_mean_return = (
        mean_return * periods_per_year
        if mean_return is not None and periods_per_year is not None
        else None
    )
    sharpe_ratio = (
        annualized_mean_return / annualized_volatility
        if annualized_mean_return is not None
        and annualized_volatility is not None
        and annualized_volatility > 1e-12
        else None
    )
    return {
        "risk_calculation_frequency": calculation_frequency,
        "risk_return_observation_count": len(returns),
        "risk_annualization_periods_per_year": periods_per_year,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe_ratio,
    }


def period_return_contracts_by_group(
    daily_slices: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    rows_by_group: dict[str, list[dict[str, object]]] = defaultdict(list)
    for daily_slice in daily_slices:
        group_key = str(daily_slice.get("group_key") or "")
        if not group_key:
            continue
        rows_by_group[group_key].append(daily_slice)

    contracts: dict[str, dict[str, object]] = {}
    for group_key, group_rows in rows_by_group.items():
        returns: list[float] = []
        invalid_active_row = False
        has_active_row = False
        for row in group_rows:
            if bool(row.get("_is_initial_valuation_anchor")):
                continue
            active_amounts = [
                _safe_float(row.get("beginning_value_base")),
                _safe_float(row.get("ending_value_base")),
                _safe_float(row.get(GROUP_CAPITAL_FLOW_IN_FIELD)),
                _safe_float(row.get(GROUP_CAPITAL_FLOW_OUT_FIELD)),
            ]
            row_is_active = any(
                value is not None and abs(value) > 1e-12
                for value in active_amounts
            )
            has_active_row = has_active_row or row_is_active
            daily_return = _safe_float(row.get("daily_return"))
            # Historical callers supplied a compact ``{group_key,
            # daily_return}`` row.  Keep that read contract valid when the
            # return itself is finite; all production slices carry an explicit
            # coverage state and therefore still fail closed.
            row_is_complete = (
                str(row.get("coverage_state") or "") == "complete"
                or (
                    "coverage_state" not in row
                    and daily_return is not None
                    and isfinite(daily_return)
                )
            )
            if (
                row_is_active
                and (
                    not row_is_complete
                    or daily_return is None
                    or not isfinite(daily_return)
                )
            ):
                invalid_active_row = True
            if (
                row_is_complete
                and daily_return is not None
                and isfinite(daily_return)
            ):
                returns.append(daily_return)

        coverage_state = "unavailable"
        period_return = None
        if returns and not invalid_active_row:
            coverage_state = "complete"
            period_return = compound_returns(returns)
        elif returns or has_active_row:
            coverage_state = "partial"
        contracts[group_key] = {
            "period_return": period_return,
            "coverage_state": coverage_state,
            "observation_count": len(returns),
        }
    return contracts


def period_returns_by_group(
    daily_slices: list[dict[str, object]],
) -> dict[str, float | None]:
    return {
        group_key: cast(float | None, contract.get("period_return"))
        for group_key, contract in period_return_contracts_by_group(
            daily_slices
        ).items()
        if contract.get("period_return") is not None
    }
