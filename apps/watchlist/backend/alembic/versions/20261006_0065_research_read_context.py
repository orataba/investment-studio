"""Materialize current research reads without parsing retained source snapshots."""
from alembic import op
import sqlalchemy as sa

from watchlist_migration_snapshots.research_read_context_0065 import derive_read_context

revision = "20261006_0065"
down_revision = "20261002_0064"
branch_labels = None
depends_on = None

SCOPE_INDEX = "ix_research_entry_instrument_scope"
PORTFOLIO_INDEX = "ix_research_entry_retained_portfolio_topic"


def _indexes(column):
    op.create_index(SCOPE_INDEX, "research_entry", [sa.text(
        f"watchlist.research_entry_instrument_scope({column})")], postgresql_using="gin")
    op.create_index(PORTFOLIO_INDEX, "research_entry", ["topic_id"], postgresql_where=sa.text(
        f"watchlist.research_entry_has_retained_portfolio_scope({column})"))


def upgrade():
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        op.drop_index(SCOPE_INDEX, table_name="research_entry")
        op.drop_index(PORTFOLIO_INDEX, table_name="research_entry")
    op.add_column("research_entry", sa.Column("read_context_json", sa.JSON(), nullable=True))
    entries = sa.table("research_entry", sa.column("entry_id", sa.String()),
        sa.column("context_json", sa.JSON()), sa.column("read_context_json", sa.JSON()))
    # A keyset row fetch bounds the client/wire buffer and releases each retained
    # original before the next. PostgreSQL returns raw json without parsing or
    # normalizing a potentially very large input snapshot. Only the new column
    # changes; original text, timestamps and source/version identities stay exact.
    last_id = None
    while True:
        query = sa.select(entries.c.entry_id, entries.c.context_json).order_by(entries.c.entry_id).limit(1)
        if last_id is not None:
            query = query.where(entries.c.entry_id > last_id)
        row = connection.execute(query).first()
        if row is None:
            break
        last_id = row.entry_id
        projection = derive_read_context(row.context_json)
        connection.execute(entries.update().where(entries.c.entry_id == last_id).values(read_context_json=projection))
        del row, projection
    with op.batch_alter_table("research_entry") as batch:
        batch.alter_column("read_context_json", existing_type=sa.JSON(), nullable=False)
    if postgres:
        _indexes("read_context_json")


def downgrade():
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        op.drop_index(SCOPE_INDEX, table_name="research_entry")
        op.drop_index(PORTFOLIO_INDEX, table_name="research_entry")
        _indexes("context_json")
    op.drop_column("research_entry", "read_context_json")
