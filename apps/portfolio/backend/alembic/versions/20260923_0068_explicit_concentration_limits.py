"""Expand dated concentration policies to explicit member limits with audit evidence."""
from collections import defaultdict
from copy import deepcopy
import json

from alembic import op
import sqlalchemy as sa

revision = "20260923_0068"
down_revision = "20260923_0067"
branch_labels = None
depends_on = None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _member_inventory(connection):
    members = defaultdict(lambda: defaultdict(set))
    for table in ("portfolio_instrument_universe_record", "transaction_record", "portfolio_daily_holding_snapshot"):
        for portfolio_id, instrument_id in connection.execute(sa.text(
            f"SELECT DISTINCT portfolio_id, instrument_id FROM {table} WHERE instrument_id IS NOT NULL"
        )):
            if instrument_id:
                members[portfolio_id][("security", None)].add(instrument_id)
    for portfolio_id, contract_id in connection.execute(sa.text(
        "SELECT portfolio_id, derivative_contract_id FROM derivative_contract_record WHERE contract_type = 'fcn'"
    )):
        members[portfolio_id][("fcn", None)].add(contract_id)
    for portfolio_id, taxonomy_id, node_id in connection.execute(sa.text(
        "SELECT t.portfolio_id, t.taxonomy_id, n.taxonomy_node_id FROM taxonomy_record t "
        "JOIN taxonomy_node_record n ON n.taxonomy_id = t.taxonomy_id"
    )):
        members[portfolio_id][("taxonomy", taxonomy_id)].add(node_id)
    # Deleted nodes may still belong to an earlier policy. Configuration revisions
    # are the identity inventory; the old policy's own explicit references join it.
    for portfolio_id, taxonomy_id, payload in connection.execute(sa.text(
        "SELECT portfolio_id, taxonomy_id, configuration_json FROM taxonomy_configuration_revision"
    )):
        for node in (_json(payload) or {}).get("taxonomy_nodes", []):
            members[portfolio_id][("taxonomy", taxonomy_id)].add(node["taxonomy_node_id"])
    for portfolio_id, instrument_id in connection.execute(sa.text(
        "SELECT DISTINCT t.portfolio_id, a.target_entity_id FROM taxonomy_assignment_record a "
        "JOIN taxonomy_record t ON t.taxonomy_id = a.taxonomy_id WHERE a.target_scope = 'instrument'"
    )):
        if instrument_id:
            members[portfolio_id][("security", None)].add(instrument_id)
    return members


def _convert(settings, members):
    rules = settings.get("rules", [])
    grouped = defaultdict(list)
    for rule in rules:
        grouped[(rule["scope"], rule.get("taxonomy_id"))].append(rule)
    enabled_taxonomy_ids, limits = [], []
    for (scope, taxonomy_id), scoped_rules in sorted(grouped.items(), key=lambda item: str(item[0])):
        default = next((rule for rule in scoped_rules if rule.get("entity_id") is None), None)
        scope_enabled = default is None or default.get("enabled", True)
        if scope == "taxonomy" and scope_enabled:
            enabled_taxonomy_ids.append(taxonomy_id)
        for entity_id in sorted(members.get((scope, taxonomy_id), set())):
            selected = next((rule for rule in scoped_rules if rule.get("entity_id") == entity_id), None)
            # A taxonomy switch preserves entered caps for later re-enabling.
            # Securities and FCNs have no switch in the new contract, so a disabled
            # former scope must not suddenly activate previously suppressed caps.
            if scope != "taxonomy" and not scope_enabled:
                continue
            if selected is None:
                selected = default
                enabled = selected is not None and (scope == "taxonomy" or selected.get("enabled", True))
            else:
                enabled = selected.get("enabled", True)
            if not enabled or selected.get("limit_weight") is None:
                continue
            limits.append({"scope": scope, "taxonomy_id": taxonomy_id, "entity_id": entity_id,
                           "limit_weight": selected["limit_weight"]})
    return {"schema_version": 2, "enabled_taxonomy_ids": enabled_taxonomy_ids, "limits": limits,
            "fcn_allocations": deepcopy(settings.get("fcn_allocations", [])),
            "migration_audit": {"migration": revision, "original_settings": deepcopy(settings)}}


def upgrade():
    connection = op.get_bind()
    table = sa.table("concentration_policy_revision", sa.column("portfolio_id", sa.String()),
                     sa.column("revision", sa.Integer()), sa.column("settings_json", sa.JSON()))
    rows = list(connection.execute(sa.select(table)))
    members = _member_inventory(connection)
    # Use identities from every revision, including future-dated rules, before
    # expanding any default. No policy date or revision ordering is changed.
    for row in rows:
        for rule in row.settings_json.get("rules", []):
            if rule.get("entity_id") is not None:
                members[row.portfolio_id][(rule["scope"], rule.get("taxonomy_id"))].add(rule["entity_id"])
    for row in rows:
        converted = _convert(row.settings_json, members[row.portfolio_id])
        connection.execute(table.update().where(table.c.portfolio_id == row.portfolio_id,
                                               table.c.revision == row.revision).values(settings_json=converted))


def downgrade():
    connection = op.get_bind()
    table = sa.table("concentration_policy_revision", sa.column("portfolio_id", sa.String()),
                     sa.column("revision", sa.Integer()), sa.column("settings_json", sa.JSON()))
    rows = list(connection.execute(sa.select(table)))
    # Copies retain the source's immutable migration evidence, but their current
    # taxonomy identities differ. That evidence cannot restore the copied policy.
    if any("copied_from_portfolio_id" in row.settings_json
           or row.settings_json.get("migration_audit", {}).get("migration") != revision for row in rows):
        raise RuntimeError("Cannot restore old concentration rules after new settings were saved; restore the pre-migration backup instead.")
    for row in rows:
        original = row.settings_json["migration_audit"]["original_settings"]
        connection.execute(table.update().where(table.c.portfolio_id == row.portfolio_id,
                                               table.c.revision == row.revision).values(settings_json=original))
