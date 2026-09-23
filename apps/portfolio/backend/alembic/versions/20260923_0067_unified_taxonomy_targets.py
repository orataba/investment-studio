"""Use one allocation basis and one scalar per stage; remove global defaults."""
from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "20260923_0067"
down_revision = "20260923_0066"
branch_labels = None
depends_on = None


def _rows(connection, table):
    return [dict(row) for row in connection.execute(sa.text(f"SELECT * FROM {table}")).mappings()]


def _target_basis(target_set, taxonomies, nodes):
    scope = target_set["comparator_taxonomy_node_id"]
    return (nodes[scope]["default_target_dimension"] if scope else
            taxonomies[target_set["taxonomy_id"]]["root_default_target_dimension"])


def _empty_scope_target_set_ids(nodes, target_sets, assignments):
    """Match move/delete cleanup for current leaves whose last member departed."""
    active_scopes = {(row["taxonomy_id"], row["taxonomy_node_id"])
                     for row in nodes if row["status"] == "active"}
    populated_scopes = {(row["taxonomy_id"], row["parent_taxonomy_node_id"])
                        for row in nodes if row["status"] == "active"}
    populated_scopes.update((row["taxonomy_id"], row["taxonomy_node_id"])
                            for row in assignments if row["status"] == "active")
    return {row["target_set_id"] for row in target_sets
            if row["status"] == "active"
            and (row["taxonomy_id"], row["comparator_taxonomy_node_id"]) in active_scopes - populated_scopes}


def convert_target_lines(taxonomies, nodes, target_sets, lines):
    """Choose the configured basis, never convert capital percentages into RC."""
    tax_by_id = {row["taxonomy_id"]: row for row in taxonomies}
    node_by_id = {row["taxonomy_node_id"]: row for row in nodes}
    result, conflicts = [], []
    for target_set in target_sets:
        scope = target_set["comparator_taxonomy_node_id"]
        basis = _target_basis(target_set, tax_by_id, node_by_id)
        enabled_field = "weight_enabled" if basis == "weight" else "risk_budget_enabled"
        # Disabled stage values were never selected by the old solver. Turning
        # them into live scalars would silently activate a dormant allocation.
        # The complete original stage remains in the migration audit snapshot.
        if not target_set[enabled_field]:
            continue
        field = "target_weight" if basis == "weight" else "target_risk_share"
        selected = [deepcopy(line) for line in lines if line["target_set_id"] == target_set["target_set_id"]]
        security = [line for line in selected if line["target_member_type"] not in {"cash_bucket", "derivative_bucket"}]
        amounts = [line.get(field) for line in security]
        for line in selected:
            if line["target_member_type"] == "derivative_bucket":
                continue
            value = line["target_weight"] if line["target_member_type"] == "cash_bucket" else line[field]
            other = line.get("target_risk_share" if field == "target_weight" else "target_weight")
            if target_set["status"] == "active" and line["target_member_type"] != "cash_bucket" and value is None and other is not None:
                conflicts.append(f"{target_set['target_set_id']} / {line['target_member_id']}: configured {basis} value is missing")
            if value is not None and (not isfinite(float(value)) or float(value) < 0):
                conflicts.append(f"{line['target_line_id']}: invalid target value")
            # Cash without an old capital target is an absent reserve, not a risk target.
            if line["target_member_type"] == "cash_bucket" and value is None:
                continue
            line["target_value"] = value
            line.pop("target_weight", None)
            line.pop("target_risk_share", None)
            result.append(line)
        total = sum(float(value) for value in amounts if value is not None)
        root_weight = scope is None and basis == "weight"
        if root_weight:
            cash_reserve = next((float(line["target_value"]) for line in result
                if line["target_set_id"] == target_set["target_set_id"] and line["target_member_type"] == "cash_bucket"), 0.0)
            if (target_set["status"] == "active" and amounts and all(value is not None for value in amounts)
                    and total == 0 and cash_reserve != 1):
                conflicts.append(f"{target_set['target_set_id']}: zero security weights with a non-full cash reserve need an explicit new allocation")
        # Root capital used to include cash and derivatives. Other vectors were
        # accepted with a 0.0005 rounding tolerance. Preserve their ratios rather
        # than turn previously valid thirds into an incomplete new vector.
        normalize = root_weight or abs(total - 1) <= 0.0005
        if normalize and amounts and all(value is not None for value in amounts) and total > 0:
            for line in result:
                if line["target_set_id"] == target_set["target_set_id"] and line["target_member_type"] not in {"cash_bucket", "derivative_bucket"}:
                    line["target_value"] = float(line["target_value"]) / total
    if conflicts:
        raise ValueError("Resolve ambiguous taxonomy targets before migration: " + "; ".join(conflicts))
    return result


def upgrade():
    connection = op.get_bind()
    taxonomies = _rows(connection, "taxonomy_record")
    nodes = _rows(connection, "taxonomy_node_record")
    target_sets = _rows(connection, "target_set_record")
    old_lines = _rows(connection, "target_set_line_record")
    assignments = _rows(connection, "taxonomy_assignment_record")
    portfolios = _rows(connection, "portfolio_record")
    settings = _rows(connection, "research_settings_record")
    # Old moves could leave targets in a now-empty leaf. The editor has no
    # members to edit there; apply the runtime's empty-scope cleanup. Do not
    # change the parent's allocation to that node or move any assignments.
    # Both removed vectors remain in original_configuration below.
    empty_scope_target_ids = _empty_scope_target_set_ids(nodes, target_sets, assignments)
    converted = convert_target_lines(taxonomies, nodes,
        [row for row in target_sets if row["target_set_id"] not in empty_scope_target_ids], old_lines)
    tax_by_id = {row["taxonomy_id"]: row for row in taxonomies}
    nodes_by_id = {row["taxonomy_node_id"]: row for row in nodes}
    for row in settings:
        override = row.get("target_dimension")
        taxonomy = tax_by_id.get(row.get("planning_taxonomy_id"))
        if taxonomy is None or override not in {"weight", "risk_budget"}:
            continue
        node = nodes_by_id.get(row.get("comparator_taxonomy_node_id"))
        basis = node["default_target_dimension"] if node else taxonomy["root_default_target_dimension"]
        if override != basis:
            raise ValueError(f"Research settings for {row['portfolio_id']} override the taxonomy allocation basis; reconcile the selected scope before migration.")

    op.add_column("target_set_line_record", sa.Column("target_value", sa.Float(), nullable=True))
    kept_ids = {row["target_line_id"] for row in converted}
    for row in old_lines:
        if row["target_line_id"] not in kept_ids:
            connection.execute(sa.text("DELETE FROM target_set_line_record WHERE target_line_id=:id"), {"id": row["target_line_id"]})
    for target_set in target_sets:
        basis = _target_basis(target_set, tax_by_id, nodes_by_id)
        enabled_field = "weight_enabled" if basis == "weight" else "risk_budget_enabled"
        if not target_set[enabled_field] or target_set["target_set_id"] in empty_scope_target_ids:
            connection.execute(sa.text("DELETE FROM target_set_record WHERE target_set_id=:id"),
                {"id": target_set["target_set_id"]})
    for row in converted:
        connection.execute(sa.text("UPDATE target_set_line_record SET target_value=:value WHERE target_line_id=:id"),
            {"id": row["target_line_id"], "value": row["target_value"]})
    checks = sa.inspect(connection).get_check_constraints("target_set_line_record")
    with op.batch_alter_table("target_set_line_record") as batch:
        for constraint in checks:
            if "target_risk_share" in constraint["sqltext"]:
                batch.drop_constraint(op.f(constraint["name"]), type_="check")
        batch.create_check_constraint("ck_target_set_line_no_derivative_target", "target_member_type != 'derivative_bucket'")
        batch.drop_column("target_weight")
        batch.drop_column("target_risk_share")
    for column in ("weight_enabled", "risk_budget_enabled"):
        op.drop_column("target_set_record", column)
    op.alter_column("taxonomy_record", "root_default_target_dimension", new_column_name="root_allocation_basis")
    op.alter_column("taxonomy_node_record", "default_target_dimension", new_column_name="allocation_basis")
    for column in ("planning_enabled", "budgeting_level"):
        op.drop_column("taxonomy_record", column)
    op.drop_column("portfolio_record", "default_planning_taxonomy_id")
    op.drop_column("research_settings_record", "target_dimension")

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    revisions = sa.table("taxonomy_configuration_revision",
        sa.column("taxonomy_configuration_revision_id", sa.String()), sa.column("portfolio_id", sa.String()),
        sa.column("taxonomy_id", sa.String()), sa.column("configuration_version", sa.Integer()),
        sa.column("configuration_json", sa.JSON()), sa.column("superseded_by_revision_id", sa.String()),
        sa.column("created_at", sa.String()))
    new_tax = {row["taxonomy_id"]: row for row in _rows(connection, "taxonomy_record")}
    new_nodes = _rows(connection, "taxonomy_node_record")
    new_sets = _rows(connection, "target_set_record")
    for taxonomy in taxonomies:
        tid, pid = taxonomy["taxonomy_id"], taxonomy["portfolio_id"]
        ids = {row["target_set_id"] for row in target_sets if row["taxonomy_id"] == tid}
        old_payload = {"taxonomy": taxonomy,
            "taxonomy_nodes": [r for r in nodes if r["taxonomy_id"] == tid],
            "taxonomy_assignments": [r for r in assignments if r["taxonomy_id"] == tid],
            "target_sets": [r for r in target_sets if r["taxonomy_id"] == tid],
            "target_set_lines": [r for r in old_lines if r["target_set_id"] in ids]}
        payload = {"taxonomy": new_tax[tid],
            "taxonomy_nodes": [r for r in new_nodes if r["taxonomy_id"] == tid],
            "taxonomy_assignments": old_payload["taxonomy_assignments"],
            "target_sets": [r for r in new_sets if r["taxonomy_id"] == tid],
            "target_set_lines": [r for r in converted if r["target_set_id"] in ids],
            "migration_audit": {"original_configuration": old_payload,
                "original_default_taxonomy_id": next((p.get("default_planning_taxonomy_id") for p in portfolios if p["portfolio_id"] == pid), None),
                "original_research_target_dimension": next((r.get("target_dimension") for r in settings if r["portfolio_id"] == pid), None)}}
        version = (connection.execute(sa.text("SELECT current_version FROM portfolio_taxonomy_state WHERE portfolio_id=:id"), {"id": pid}).scalar() or 0) + 1
        updated = connection.execute(sa.text("UPDATE portfolio_taxonomy_state SET current_version=:v, updated_at=:now WHERE portfolio_id=:id"), {"id": pid, "v": version, "now": now})
        if updated.rowcount == 0:
            connection.execute(sa.text("INSERT INTO portfolio_taxonomy_state(portfolio_id,current_version,updated_at) VALUES(:id,:v,:now)"), {"id": pid, "v": version, "now": now})
        rid = f"taxonomy-revision-{uuid4().hex}"
        connection.execute(revisions.update().where(revisions.c.taxonomy_id == tid, revisions.c.superseded_by_revision_id.is_(None)).values(superseded_by_revision_id=rid))
        connection.execute(revisions.insert().values(taxonomy_configuration_revision_id=rid, portfolio_id=pid,
            taxonomy_id=tid, configuration_version=version, configuration_json=payload,
            superseded_by_revision_id=None, created_at=now))
    connection.execute(sa.text("UPDATE portfolio_calculation_state SET daily_snapshot_status='stale', dirty_from=NULL, error_message=NULL"))
    connection.execute(sa.text("DELETE FROM portfolio_workspace_read_model"))


def downgrade():
    raise RuntimeError("Unified targets cannot reconstruct discarded alternate dimensions; restore the pre-migration backup.")
