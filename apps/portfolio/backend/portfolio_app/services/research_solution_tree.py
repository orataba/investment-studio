"""Frozen research-result presentation; never consult the live taxonomy or prices."""
from __future__ import annotations

from math import isfinite

from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets

SOLUTION_TREE_SCHEMA_VERSION = 2


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


def _saved_portfolio_nav(detail, request, leaf_records, valuation):
    """Recover an archive's NAV only from a reconciled full-portfolio capital tape."""
    nav = _number((valuation or {}).get("portfolio_nav"))
    if nav is None:
        nav = _number((detail.get("solution_tree") or {}).get("portfolio_nav"))
    if nav is not None:
        return nav
    scope = detail.get("selected_scope") or detail.get("scope") or {}
    full_scope = ("taxonomy_node_id" in scope and scope["taxonomy_node_id"] is None
                  or "comparator_taxonomy_node_id" in request and request["comparator_taxonomy_node_id"] is None)
    if not full_scope or scope.get("taxonomy_node_id") or detail.get("risk_attribution_scope") == "selected_research_scope":
        return None
    values = [_number(row.get("current_value_base")) for row in leaf_records.values()]
    weights = [_number(row.get("current_weight")) for row in leaf_records.values()]
    if not values or any(value is None for value in [*values, *weights]):
        return None
    total = sum(values)
    if total <= 0 or abs(sum(weights) - 1.0) > 1e-6:
        return None
    return total if all(abs(value / total - weight) <= 1e-6 for value, weight in zip(values, weights, strict=True)) else None


def _global_risk_targets(detail, configuration, scope_id):
    if configuration.get("snapshot_schema_version") == 3:
        # Always start at the frozen portfolio root. Starting at the selected
        # sleeve would silently renormalize a 20% portfolio budget to 100%.
        rows = resolve_taxonomy_targets(configuration).get("member_targets", [])
        return {(row["member_type"], row["member_id"]): _number(row.get("tactical_global_risk_target")) for row in rows}
    if scope_id is not None:
        return {}
    # Pre-snapshot archives retain only root members and terminal member paths.
    # A root budget and a directly nested Risk member prove their product;
    # missing intermediate budgets cannot be inferred from solved allocations.
    result = {}
    root_members = {}
    for row in detail.get("member_targets") or []:
        if row.get("selected_target_dimension") != "risk_budget":
            continue
        value = _number(row.get("configured_risk_share"))
        if row.get("member_type") in {"taxonomy_node", "instrument"} and value is not None:
            result[(row["member_type"], row["member_id"])] = value
            if row.get("member_type") == "taxonomy_node":
                root_members[row["member_id"]] = row
    groups = {row["member_id"]: group.get("top_sleeve_id") for group in detail.get("solved_result_groups") or [] for row in group.get("rows") or []}
    scope_paths = {row["scope_node_id"]: row["scope_path"] for row in detail.get("scope_solve_events") or []
                   if row.get("scope_node_id") and row.get("scope_path")}
    for row in detail.get("leaf_targets") or []:
        parent = root_members.get(groups.get(row.get("member_id"))) or {}
        parent_path = parent.get("member_path") or scope_paths.get(parent.get("member_id"))
        if (row.get("selected_target_dimension") == "risk_budget" and parent_path
                and row.get("scope_path") == parent_path):
            value = _number(row.get("configured_risk_share"))
            if value is not None:
                result[(row["member_type"], row["member_id"])] = result[("taxonomy_node", parent["member_id"])] * value
    return result


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
    nav = _saved_portfolio_nav(detail, request, leaf_records, valuation)
    denominator = nav if nav is not None and nav > 0 else None
    nodes = {row["taxonomy_node_id"]: row for row in configuration.get("taxonomy_nodes") or [] if row.get("status", "active") == "active"}
    assignments = {row["target_entity_id"]: row["taxonomy_node_id"] for row in configuration.get("taxonomy_assignments") or [] if row.get("status", "active") == "active" and row.get("target_scope") == "instrument"}
    target_risk = _global_risk_targets(detail, configuration, scope_id)
    has_solved_attribution = any(_number(row.get("forward_risk_contribution")) is not None for row in leaf_records.values()
                                 if row.get("risk_model_status", "modeled") != "excluded")
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
               "current_value_base": None, "current_weight": None,
               "target_value_base": None, "target_weight": None, "rebalance_value_base": None,
               "trade_constraint": "adjustable", "risk_model_status": "modeled",
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
                      target_risk_share=target_risk.get(("taxonomy_node", node_id)),
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
                target_risk_share=target_risk.get(("taxonomy_node", key)), min_weight=_number(group.get("min_weight")),
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
        kind = "cash" if member_type == "cash_bucket" else "derivatives" if member_type == "derivative_bucket" else "category" if member_type == "taxonomy_node" else "instrument"
        if kind in {"cash", "derivatives"}:
            parent_row_id = "root"
        current = _number(source.get("current_value_base"))
        target = _number(gap.get("target_value_base", source.get("target_value_base")))
        constraint = gap.get("trade_constraint") or source.get("trade_constraint") or "adjustable"
        if constraint == "no_trade" and current is not None:
            target = current
        solved_rc = _number(source.get("forward_risk_contribution")) if kind in {"instrument", "category"} else None
        if (solved_rc is None and kind in {"instrument", "category"} and has_solved_attribution
                and source.get("risk_model_status", "modeled") == "modeled" and _number(source.get("solved_weight")) == 0):
            # A modeled zero allocation contributes exactly zero under the
            # saved covariance. Missing nonzero contributions remain unknown.
            solved_rc = 0.0
        if kind == "category" and f"node:{member_id}" in by_id:
            continue  # A zero-terminal scope is already present in the frozen tree.
        add(f"{member_type}:{member_id}", parent_row_id, kind, member_type, member_id, str(source.get("label") or member_id),
            current_value_base=current, target_value_base=target,
            current_weight=current / denominator if current is not None and denominator is not None else None,
            target_weight=target / denominator if target is not None and denominator is not None else _number(source.get("solved_weight")),
            rebalance_value_base=target - current if target is not None and current is not None else None,
            target_risk_share=target_risk.get(key) if kind in {"instrument", "category"} else None,
            solved_risk_share=solved_rc, trade_constraint=constraint,
            risk_model_status=source.get("risk_model_status") or ("excluded" if kind != "instrument" else "modeled"),
            execution_status=gap.get("execution_status") or ("no_trade" if constraint == "no_trade" else "ready"), execution_note=gap.get("execution_note"))

    for row in reversed(rows):
        if row["row_kind"] not in {"portfolio", "category"}:
            continue
        direct = [child for child in rows if child["parent_row_id"] == row["row_id"]]
        if not direct and row["current_value_base"] is not None:
            continue
        for field in ("current_value_base", "target_value_base", "target_weight", "rebalance_value_base"):
            row[field] = _sum(child[field] for child in direct)
        row["current_weight"] = row["current_value_base"] / denominator if row["current_value_base"] is not None and denominator is not None else None
        risk_children = [child for child in direct if child["risk_model_status"] != "excluded"]
        row["solved_risk_share"] = _sum(child["solved_risk_share"] for child in risk_children) if risk_children else (0.0 if not direct and has_solved_attribution else None)
        if direct:
            for field in ("trade_constraint", "risk_model_status"):
                values = {child[field] for child in direct}
                row[field] = next(iter(values)) if len(values) == 1 else "mixed"
            if any(child["execution_status"] == "manual_review_required" for child in direct):
                row["execution_status"] = "manual_review_required"
            elif row["trade_constraint"] == "no_trade":
                row["execution_status"] = "no_trade"
    root["target_risk_share"] = target_risk.get(("taxonomy_node", scope_id)) if scope_id else (1.0 if target_risk else None)
    # Return a depth-first tree; iteration above deliberately aggregates direct
    # children only, never both a category subtotal and its underlying leaves.
    ordered = []
    def visit(row):
        ordered.append(row)
        for child in rows:
            if child["parent_row_id"] == row["row_id"]:
                visit(child)
    visit(root)
    return {"schema_version": SOLUTION_TREE_SCHEMA_VERSION, "as_of_date": request.get("as_of_date"), "base_currency": base_currency,
            "portfolio_nav": nav, "risk_attribution_scope": risk_scope,
            "capital_weight_basis": "portfolio_nav" if denominator is not None else "saved_scope",
            "hierarchy_status": "complete" if taxonomy else "recorded_groups_only",
            "configuration_captured_at": configuration.get("captured_at"),
            "rows": ordered}
