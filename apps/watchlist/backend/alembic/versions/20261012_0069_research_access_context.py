"""Keep permission checks independent of retained analytical context size."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_0069"
down_revision = "20261009_0068"
branch_labels = None
depends_on = None

PORTFOLIO_INDEX = "ix_research_entry_retained_portfolio_topic"


def _access_context(context):
    # Frozen migration contract: do not import mutable application projections.
    if not isinstance(context, dict):
        return context
    return {key: value for key, value in context.items()
            if key in {"research_actor", "execution", "portfolio_id", "risk_scope"}}


def _portfolio_index(column):
    op.create_index(PORTFOLIO_INDEX, "research_entry", ["topic_id"], postgresql_where=sa.text(
        f"watchlist.research_entry_has_retained_portfolio_scope({column})"))


def upgrade():
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    op.add_column("research_entry", sa.Column("access_context_json", sa.JSON(), nullable=True))
    entries = sa.table("research_entry", sa.column("entry_id", sa.String()),
        sa.column("read_context_json", sa.JSON()), sa.column("access_context_json", sa.JSON()))
    # One row at a time bounds both the driver buffer and decoded JSON. The
    # existing read projection holds these exact fields, including NUL escapes;
    # do not load originals or change either retained context or timestamps.
    last_id = None
    while True:
        query = sa.select(entries.c.entry_id, entries.c.read_context_json).order_by(entries.c.entry_id).limit(1)
        if last_id is not None:
            query = query.where(entries.c.entry_id > last_id)
        row = connection.execute(query).first()
        if row is None:
            break
        last_id = row.entry_id
        projection = _access_context(row.read_context_json)
        connection.execute(entries.update().where(entries.c.entry_id == last_id).values(access_context_json=projection))
        del row, projection
    with op.batch_alter_table("research_entry") as batch:
        batch.alter_column("access_context_json", existing_type=sa.JSON(), nullable=False)
    if postgres:
        op.drop_index(PORTFOLIO_INDEX, table_name="research_entry")
        _portfolio_index("access_context_json")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index(PORTFOLIO_INDEX, table_name="research_entry")
        _portfolio_index("read_context_json")
    op.drop_column("research_entry", "access_context_json")
