"""Frozen research-result presentation; never consult the live taxonomy or prices."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import isfinite

from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _sum(values):
    items = list(values)
    return None if any(value is None for value in items) else sum(items)


def read_solution_exposures(portfolio_id: str, *, as_of_date: date, base_currency: str, nav: float | None) -> dict:
    """Use the published account-level exposure inputs, without rebuilding holdings."""
    from sqlalchemy import select
    from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailyHoldingSnapshotModel, PortfolioDailySnapshotModel
    from portfolio_app.db.session import get_session_factory
    from portfolio_app.services.concentration import project_portfolio_concentration

    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        published = session.scalar(select(PortfolioDailySnapshotModel).where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailySnapshotModel.as_of_date == as_of_date))
        if state is None or state.daily_snapshot_status != "current" or published is None or published.valuation_coverage_state != "complete":
            return {}
        records = list(session.scalars(select(PortfolioDailyHoldingSnapshotModel).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailyHoldingSnapshotModel.as_of_date == as_of_date)))
        holdings = [{**deepcopy(row.holding_json), "account_id": row.account_id,
                     "position_reference_id": row.position_reference_id, "holding_kind": row.holding_kind,
                     "instrument_id": row.instrument_id, "derivative_contract_id": row.derivative_contract_id,
                     "quantity": row.quantity, "market_value_base": row.market_value_base} for row in records]
    projection = project_portfolio_concentration(
        {"portfolio_id": portfolio_id, "as_of_date": as_of_date.isoformat(), "base_currency": base_currency,
         "totals": {"nav": nav}, "rows": holdings}, {"taxonomies": []}, holding_rows=holdings)
    exposures = {}
    for scope in projection["scopes"]:
        if scope["scope"] == "security":
            exposures.update({f"instrument:{row['entity_id']}": {"amount_base": row["exposure_base"]} for row in scope["rows"]})
        elif scope["scope"] == "fcn":
            exposures["derivative_bucket:__derivatives__"] = {"amount_base":
                None if projection["excluded_option_positions"] else _sum(row["exposure_base"] for row in scope["rows"])}
    return {"exposures": exposures, "exposure_snapshot_complete": True}


def build_research_solution_tree(detail: dict, request: dict, *, valuation: dict | None = None) -> dict | None:
    groups = detail.get("solved_result_groups") or []
    if not groups:
        return None
    configuration = request.get("target_configuration_snapshot") or {}
    taxonomy = configuration.get("taxonomy") or {}
    scope = detail.get("selected_scope") or detail.get("scope") or {}
    scope_id = scope.get("taxonomy_node_id")
    risk_scope = detail.get("risk_attribution_scope") or ("selected_research_scope" if scope_id else "portfolio")
    base_currency = configuration.get("base_currency") or request.get("base_currency")
    leaf_records = {}
    for group in groups:
        for row in group.get("rows") or []:
            # A leaf contributes exactly once, even if an old group catalogue repeats it.
            leaf_records.setdefault((row["member_type"], row["member_id"]), row)
    gaps = {(row["member_type"], row["member_id"]): row for row in detail.get("target_weight_gaps") or []}
    nav = _number((valuation or {}).get("portfolio_nav"))
    denominator = nav if nav is not None and nav > 0 else None
    exposures = (valuation or {}).get("exposures") or {}
    nodes = {row["taxonomy_node_id"]: row for row in configuration.get("taxonomy_nodes") or [] if row.get("status", "active") == "active"}
    assignments = {row["target_entity_id"]: row["taxonomy_node_id"] for row in configuration.get("taxonomy_assignments") or [] if row.get("status", "active") == "active" and row.get("target_scope") == "instrument"}
    target_rows = resolve_taxonomy_targets(configuration, scope_node_id=scope_id).get("member_targets", []) if configuration.get("snapshot_schema_version") == 3 else []
    target_risk = {(row["member_type"], row["member_id"]): _number(row.get("tactical_global_risk_target")) for row in target_rows}
    group_by_id = {row.get("top_sleeve_id"): row for row in groups}
    frozen_ids = set(request.get("frozen_taxonomy_node_ids") or [])
    recorded_parent_by_key = {}
    rows = []
    by_id = {}

    def add(row_id, parent_id, kind, member_type, member_id, label, **values):
        parent = by_id.get(parent_id)
        row = {"row_id": row_id, "parent_row_id": parent_id, "row_kind": kind,
               "member_type": member_type, "member_id": member_id, "label": label,
               "depth": parent["depth"] + 1 if parent else 0,
               "path": [*(parent["path"] if parent else []), label],
               "target_risk_share": None, "solved_risk_share": None,
               "current_value_base": None, "current_exposure_base": None, "current_exposure_weight": None,
               "target_value_base": None, "target_weight": None, "rebalance_value_base": None,
               "exposure_status": "unavailable", "trade_constraint": "adjustable", "risk_model_status": "modeled",
               "execution_status": "ready", "execution_note": None, "min_weight": None, "max_weight": None,
               "bound_status": None, **values}
        rows.append(row); by_id[row_id] = row
        return row

    root_label = str(nodes.get(scope_id, {}).get("node_name") or taxonomy.get("name") or scope.get("label") or "Portfolio")
    root = add("root", None, "portfolio", "taxonomy_node", scope_id or str(taxonomy.get("taxonomy_id") or "__root__"), root_label)
    children = {}
    for node_id, node in nodes.items():
        children.setdefault(node.get("parent_taxonomy_node_id") or None, []).append(node_id)

    def add_nodes(parent_node_id, parent_row_id):
        for node_id in sorted(children.get(parent_node_id, []), key=lambda key: (int(nodes[key].get("sort_order") or 0), str(nodes[key].get("node_name") or key), key)):
            node = nodes[node_id]
            group = group_by_id.get(node_id) or {}
            row = add(f"node:{node_id}", parent_row_id, "category", "taxonomy_node", node_id, str(node.get("node_name") or node_id),
                      target_risk_share=target_risk.get(("taxonomy_node", node_id), _number(group.get("target_risk_share"))),
                      trade_constraint="no_trade" if node_id in frozen_ids else "adjustable",
                      min_weight=_number(group.get("min_weight")), max_weight=_number(group.get("max_weight")), bound_status=group.get("bound_status"))
            add_nodes(node_id, row["row_id"])

    if nodes:
        add_nodes(scope_id, "root")
    else:
        for index, group in enumerate(groups):
            if all(row.get("member_type") in {"cash_bucket", "derivative_bucket"} for row in group.get("rows") or []):
                continue
            key = group.get("top_sleeve_id") or f"recorded-{index}"
            add(f"node:{key}", "root", "category", "taxonomy_node", key, str(group.get("top_sleeve_label") or key),
                target_risk_share=_number(group.get("target_risk_share")), min_weight=_number(group.get("min_weight")),
                max_weight=_number(group.get("max_weight")), bound_status=group.get("bound_status"))
            for leaf in group.get("rows") or []:
                recorded_parent_by_key.setdefault((leaf["member_type"], leaf["member_id"]), key)

    for key, source in leaf_records.items():
        member_type, member_id = key
        gap = gaps.get(key) or {}
        parent_node_id = assignments.get(member_id) or source.get("top_sleeve_id") or recorded_parent_by_key.get(key)
        parent_row_id = f"node:{parent_node_id}"
        if parent_node_id == scope_id or parent_row_id not in by_id:
            parent_row_id = "root"
        kind = "cash" if member_type == "cash_bucket" else "derivatives" if member_type == "derivative_bucket" else "instrument"
        if kind != "instrument":
            parent_row_id = "root"
        current = _number(source.get("current_value_base"))
        target = _number(gap.get("target_value_base", source.get("target_value_base")))
        constraint = gap.get("trade_constraint") or source.get("trade_constraint") or "adjustable"
        if constraint == "no_trade" and current is not None:
            target = current
        exposure = exposures.get(f"{member_type}:{member_id}")
        if exposure is None and kind == "instrument" and (valuation or {}).get("exposure_snapshot_complete"):
            exposure = {"amount_base": 0.0}
        exposure_value = _number((exposure or {}).get("amount_base"))
        exposure_status = "not_applicable" if kind == "cash" else "complete" if exposure_value is not None else "unavailable"
        add(f"{member_type}:{member_id}", parent_row_id, kind, member_type, member_id, str(source.get("label") or member_id),
            current_value_base=current, target_value_base=target,
            target_weight=target / denominator if target is not None and denominator is not None else _number(source.get("solved_weight")),
            rebalance_value_base=target - current if target is not None and current is not None else None,
            target_risk_share=_number(source.get("target_risk_share")) if kind == "instrument" else None,
            solved_risk_share=_number(source.get("forward_risk_contribution")) if kind == "instrument" else None,
            current_exposure_base=exposure_value, current_exposure_weight=exposure_value / denominator if exposure_value is not None and denominator is not None else None,
            exposure_status=exposure_status, trade_constraint=constraint,
            risk_model_status=source.get("risk_model_status") or ("excluded" if kind != "instrument" else "modeled"),
            execution_status=gap.get("execution_status") or ("no_trade" if constraint == "no_trade" else "ready"), execution_note=gap.get("execution_note"))

    for row in reversed(rows):
        if row["row_kind"] not in {"portfolio", "category"}:
            continue
        direct = [child for child in rows if child["parent_row_id"] == row["row_id"]]
        for field in ("current_value_base", "target_value_base", "target_weight", "rebalance_value_base"):
            row[field] = _sum(child[field] for child in direct)
        risk_children = [child for child in direct if child["risk_model_status"] != "excluded"]
        row["solved_risk_share"] = _sum(child["solved_risk_share"] for child in risk_children) if risk_children else (0.0 if not direct else None)
        exposure_children = [child for child in direct if child["exposure_status"] != "not_applicable"]
        row["current_exposure_base"] = _sum(child["current_exposure_base"] for child in exposure_children)
        row["current_exposure_weight"] = row["current_exposure_base"] / denominator if row["current_exposure_base"] is not None and denominator is not None else None
        row["exposure_status"] = "unavailable" if row["current_exposure_base"] is None else "complete"
        if direct:
            for field in ("trade_constraint", "risk_model_status"):
                values = {child[field] for child in direct}
                row[field] = next(iter(values)) if len(values) == 1 else "mixed"
            if any(child["execution_status"] == "manual_review_required" for child in direct):
                row["execution_status"] = "manual_review_required"
            elif row["trade_constraint"] == "no_trade":
                row["execution_status"] = "no_trade"
    root["target_risk_share"] = 1.0 if any(row["risk_model_status"] == "modeled" and row["row_kind"] == "instrument" for row in rows) else None
    # Return a depth-first tree; iteration above deliberately aggregates direct
    # children only, never both a category subtotal and its underlying leaves.
    ordered = []
    def visit(row):
        ordered.append(row)
        for child in rows:
            if child["parent_row_id"] == row["row_id"]:
                visit(child)
    visit(root)
    return {"schema_version": 1, "as_of_date": request.get("as_of_date"), "base_currency": base_currency,
            "portfolio_nav": nav, "risk_attribution_scope": risk_scope,
            "capital_weight_basis": "portfolio_nav" if denominator is not None else "saved_scope",
            "hierarchy_status": "complete" if taxonomy else "recorded_groups_only",
            "configuration_captured_at": configuration.get("captured_at"),
            "rows": ordered}
