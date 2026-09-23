"""Resolve the one current allocation target model for UI, risk and Research.

Each parent chooses one allocation basis. SAA and TAA are scalar vectors in
that basis; only an entirely absent TAA vector inherits SAA. Cash is a separate
root NAV reserve and derivatives have no editable allocation target.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from math import isfinite


TARGET_RESOLUTION_VERSION = 1
CASH_MEMBER_ID = "__cash__"


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def resolve_taxonomy_targets(configuration: dict[str, object], *, scope_node_id: str | None = None) -> dict[str, object]:
    """Return JSON-ready effective vectors and static global risk budgets.

    Configuration has the same shape as a frozen current-taxonomy snapshot.
    Errors are data in this read model; a solver rejects an invalid scope when
    that scope actually participates in its selected research allocation.
    """
    taxonomy = dict(configuration.get("taxonomy") or {})
    taxonomy_id = str(taxonomy.get("taxonomy_id") or "")
    nodes = {
        str(row["taxonomy_node_id"]): row
        for row in configuration.get("taxonomy_nodes") or []
        if row.get("status", "active") == "active"
        and row.get("taxonomy_id", taxonomy_id) == taxonomy_id
    }
    children: dict[str | None, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        children[node.get("parent_taxonomy_node_id") or None].append(node_id)
    for siblings in children.values():
        siblings.sort(key=lambda node_id: (int(nodes[node_id].get("sort_order") or 0), str(nodes[node_id].get("node_name") or node_id), node_id))
    excluded = set(configuration.get("contract_only_instrument_ids") or [])
    instrument_labels = dict(configuration.get("instrument_labels") or {})
    assignments: dict[str, set[str]] = defaultdict(set)
    for row in configuration.get("taxonomy_assignments") or []:
        if (row.get("status", "active") == "active"
            and row.get("taxonomy_id", taxonomy_id) == taxonomy_id
            and row.get("target_scope") == "instrument"
            and row.get("target_entity_id") not in excluded):
            assignments[str(row.get("taxonomy_node_id") or "")].add(str(row["target_entity_id"]))
    sets: dict[tuple[str | None, str], list[dict]] = defaultdict(list)
    for row in configuration.get("target_sets") or []:
        if row.get("status", "active") == "active" and row.get("taxonomy_id", taxonomy_id) == taxonomy_id:
            sets[(row.get("comparator_taxonomy_node_id") or None, str(row.get("target_set_type") or ""))].append(row)
    lines: dict[str, list[dict]] = defaultdict(list)
    for row in configuration.get("target_set_lines") or []:
        lines[str(row.get("target_set_id") or "")].append(row)

    scopes: list[dict[str, object]] = []
    members: list[dict[str, object]] = []
    errors: list[str] = []

    def stage_vector(scope_id: str | None, basis: str, scope_members: list[dict], stage: str) -> dict:
        candidates = sets.get((scope_id, stage), [])
        label = str(nodes.get(scope_id, {}).get("node_name") or taxonomy.get("name") or "Portfolio")
        result = {"status": "missing", "source_stage": None, "source_target_set_id": None,
                  "inherited": False, "rows": [], "errors": []}
        if len(candidates) > 1:
            result.update(status="invalid", errors=[f"{label} has multiple active {stage.upper()} target vectors."])
            return result
        target_set = candidates[0] if candidates else None
        raw_lines = lines.get(str((target_set or {}).get("target_set_id") or ""), [])
        configured = [row for row in raw_lines if row.get("target_value") is not None]
        securities = [row for row in scope_members if row["member_type"] != "cash_bucket"]
        if not configured:
            # A sole security has no allocation choice. The provenance remains
            # explicit; no equal-weight fallback is applied to multiple members.
            if stage == "saa" and len(securities) == 1:
                result.update(status="complete", source_stage="single_member", rows=[
                    {**row, "target_value": 0.0 if row["member_type"] == "cash_bucket" else 1.0,
                     "target_basis": "weight" if row["member_type"] == "cash_bucket" else basis}
                    for row in scope_members])
            return result
        result.update(source_stage=stage, source_target_set_id=target_set.get("target_set_id"))
        line_map: dict[tuple[str, str], dict] = {}
        allowed = {(row["member_type"], row["member_id"]) for row in scope_members}
        for line in raw_lines:
            key = (str(line.get("target_member_type") or ""), str(line.get("target_member_id") or ""))
            if key in line_map:
                result["errors"].append(f"{label} {stage.upper()} has a duplicate target member: {key[1]}.")
            if key not in allowed:
                result["errors"].append(f"{label} {stage.upper()} contains a member outside this allocation scope: {key[1]}.")
            line_map[key] = line
        for member in scope_members:
            cash = member["member_type"] == "cash_bucket"
            raw = line_map.get((member["member_type"], member["member_id"]), {}).get("target_value")
            value = 0.0 if cash and raw is None else _number(raw)
            if value is None:
                result["errors"].append(f"{label} {stage.upper()} target set is incomplete; missing a finite target for {member['label']}.")
            elif not 0.0 <= value <= 1.0:
                result["errors"].append(f"{label} {stage.upper()} targets must be between 0% and 100%.")
            result["rows"].append({**member, "target_value": value, "target_basis": "weight" if cash else basis})
        security_values = [row["target_value"] for row in result["rows"] if row["member_type"] != "cash_bucket"]
        cash_reserve = next((row["target_value"] for row in result["rows"] if row["member_type"] == "cash_bucket"), 0.0)
        if all(value is not None for value in security_values):
            total = sum(security_values)
            all_cash = scope_id is None and basis == "weight" and cash_reserve == 1.0 and abs(total) <= 1e-12
            if not all_cash and (not security_values or abs(total - 1.0) > 1e-6):
                result["errors"].append(f"{label} {stage.upper()} security targets must sum to 1.000000; got {total:.6f}.")
        result["status"] = "invalid" if result["errors"] else "complete"
        return result

    visited: set[str | None] = set()

    def walk(scope_id: str | None, strategic_global: float | None, tactical_global: float | None) -> None:
        if scope_id in visited:
            errors.append("Taxonomy allocation hierarchy contains a cycle or repeated node.")
            return
        visited.add(scope_id)
        basis = str((taxonomy.get("root_allocation_basis") if scope_id is None else nodes[scope_id].get("allocation_basis")) or "weight")
        scope_members = [
            {"member_type": "taxonomy_node", "member_id": node_id, "taxonomy_node_id": node_id,
             "label": str(nodes[node_id].get("node_name") or node_id)}
            for node_id in children.get(scope_id, [])
        ]
        if not scope_members and scope_id is not None:
            scope_members = [{"member_type": "instrument", "member_id": instrument_id,
                              "taxonomy_node_id": scope_id, "label": str(instrument_labels.get(instrument_id) or instrument_id)}
                             for instrument_id in sorted(assignments.get(scope_id, set()))]
        if scope_id is None:
            scope_members.append({"member_type": "cash_bucket", "member_id": CASH_MEMBER_ID,
                                  "taxonomy_node_id": None, "label": "Cash"})
        saa = stage_vector(scope_id, basis, scope_members, "saa")
        taa = stage_vector(scope_id, basis, scope_members, "taa")
        if taa["status"] == "missing":
            taa = {**deepcopy(saa), "inherited": saa["status"] == "complete"}
        scopes.append({"scope_node_id": scope_id, "allocation_basis": basis, "saa": saa, "taa": taa})
        errors.extend(saa["errors"])
        errors.extend(taa["errors"])
        maps = [{(row["member_type"], row["member_id"]): row for row in stage["rows"]} for stage in (saa, taa)]
        security_count = sum(row["member_type"] != "cash_bucket" for row in scope_members)
        for member in scope_members:
            key = (member["member_type"], member["member_id"])
            values = [mapping.get(key, {}).get("target_value") for mapping in maps]
            global_targets = []
            for parent_global, value, stage in zip((strategic_global, tactical_global), values, (saa, taa), strict=True):
                if member["member_type"] == "cash_bucket" or parent_global is None:
                    target = None
                elif parent_global == 0.0:
                    target = 0.0
                elif stage["status"] != "complete" or value is None:
                    target = None
                elif basis == "risk_budget":
                    target = parent_global * value
                elif security_count == 1 and value > 0.0:
                    target = parent_global
                else:
                    target = None
                global_targets.append(target)
            members.append({**member, "scope_node_id": scope_id,
                            "target_basis": "weight" if member["member_type"] == "cash_bucket" else basis,
                            "strategic_value": values[0], "tactical_value": values[1],
                            "strategic_source": saa["source_stage"], "tactical_source": taa["source_stage"],
                            "strategic_target_set_id": saa["source_target_set_id"], "tactical_target_set_id": taa["source_target_set_id"],
                            "strategic_global_risk_target": global_targets[0], "tactical_global_risk_target": global_targets[1],
                            "strategic_status": saa["status"], "tactical_status": taa["status"]})
            if member["member_type"] == "taxonomy_node":
                walk(member["member_id"], *global_targets)

    walk(scope_node_id, 1.0, 1.0)
    return {"taxonomy_id": taxonomy_id, "resolution_version": TARGET_RESOLUTION_VERSION,
            "scope_targets": scopes, "member_targets": members, "errors": list(dict.fromkeys(errors))}
