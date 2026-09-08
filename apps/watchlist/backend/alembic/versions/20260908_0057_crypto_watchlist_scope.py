"""Include crypto in universal research/price fields and its own taxonomy.

Revision ID: 20260908_0057
Revises: 20260908_0056
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "20260908_0057"
down_revision = "20260908_0056"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    common_types = {"public_fund", "private_fund", "etf", "equity", "index"}
    for name, key in (("field_registry", "field_key"), ("instrument_attribute_definition", "attribute_key")):
        table = sa.table(name, sa.column(key, sa.String), sa.column("instrument_scope_json", sa.JSON))
        for row in connection.execute(sa.select(table)).mappings():
            scope = json.loads(row["instrument_scope_json"]) if isinstance(row["instrument_scope_json"], str) else row["instrument_scope_json"]
            # Asset-specific fund, ETF and company fields remain asset-specific.
            if scope and set(scope) == common_types:
                connection.execute(table.update().where(table.c[key] == row[key]).values(instrument_scope_json=[*scope, "crypto"]))
    taxonomy = sa.table("instrument_taxonomy_node",
        sa.column("node_id", sa.String), sa.column("taxonomy_code", sa.String),
        sa.column("instrument_type", sa.String), sa.column("label", sa.String),
        sa.column("parent_node_id", sa.String), sa.column("level_index", sa.Integer),
        sa.column("display_order", sa.Integer), sa.column("is_leaf", sa.Boolean),
        sa.column("path_labels_json", sa.JSON), sa.column("path_node_ids_json", sa.JSON))
    if connection.execute(sa.select(taxonomy.c.node_id).where(taxonomy.c.node_id == "crypto-native")).first() is None:
        connection.execute(taxonomy.insert().values(node_id="crypto-native", taxonomy_code="instrument_taxonomy",
            instrument_type="crypto", label="原生加密资产", parent_node_id=None, level_index=1,
            display_order=99, is_leaf=True, path_labels_json=["原生加密资产"], path_node_ids_json=["crypto-native"]))


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM instrument_detail WHERE instrument_type = 'crypto' LIMIT 1")).first() or connection.execute(
        sa.text("SELECT 1 FROM instrument_taxonomy_assignment WHERE node_id = 'crypto-native' LIMIT 1")
    ).first():
        raise RuntimeError("Crypto research or taxonomy assignments exist; restore the pre-migration backup instead of discarding them.")
    all_types = {"public_fund", "private_fund", "etf", "equity", "index", "crypto"}
    for name, key in (("field_registry", "field_key"), ("instrument_attribute_definition", "attribute_key")):
        table = sa.table(name, sa.column(key, sa.String), sa.column("instrument_scope_json", sa.JSON))
        for row in connection.execute(sa.select(table)).mappings():
            scope = json.loads(row["instrument_scope_json"]) if isinstance(row["instrument_scope_json"], str) else row["instrument_scope_json"]
            if scope and set(scope) == all_types:
                connection.execute(table.update().where(table.c[key] == row[key]).values(instrument_scope_json=[item for item in scope if item != "crypto"]))
    connection.execute(sa.text("DELETE FROM instrument_taxonomy_node WHERE node_id = 'crypto-native'"))
