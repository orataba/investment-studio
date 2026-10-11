"""Separate risk delivery state and materialize bounded decision inputs."""
from alembic import op
import sqlalchemy as sa
from watchlist_migration_snapshots.research_read_context_0067 import derive_read_context

revision = "20261009_0067"
down_revision = "20261009_0066"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_risk_read_state",
        sa.Column("run_id", sa.String(), sa.ForeignKey("research_entry.entry_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("delivered_pages", sa.JSON(), nullable=False),
        sa.Column("submitted_review", sa.JSON(), nullable=True))
    connection = op.get_bind()
    entries = sa.table("research_entry", sa.column("entry_id", sa.String()),
        sa.column("context_json", sa.JSON()), sa.column("read_context_json", sa.JSON()))
    states = sa.table("research_risk_read_state", sa.column("run_id", sa.String()),
        sa.column("attempt", sa.Integer()), sa.column("delivered_pages", sa.JSON()), sa.column("submitted_review", sa.JSON()))
    last_id = None
    while True:
        query = sa.select(entries.c.entry_id, entries.c.context_json).order_by(entries.c.entry_id).limit(1)
        if last_id is not None:
            query = query.where(entries.c.entry_id > last_id)
        row = connection.execute(query).first()
        if row is None:
            break
        last_id = row.entry_id
        context = row.context_json or {}
        connection.execute(entries.update().where(entries.c.entry_id == last_id)
            .values(read_context_json=derive_read_context(context)))
        if context.get("risk_run"):
            connection.execute(states.insert().values(run_id=last_id,
                attempt=(context.get("execution") or {}).get("attempt", 1),
                delivered_pages=context.get("risk_delivered_pages", []),
                submitted_review=context.get("submitted_risk_review")))
        del row, context


def downgrade():
    # Preserve accepted submissions and receipts if reverting the storage split.
    connection = op.get_bind()
    entries = sa.table("research_entry", sa.column("entry_id", sa.String()), sa.column("context_json", sa.JSON()))
    states = sa.table("research_risk_read_state", sa.column("run_id", sa.String()),
        sa.column("delivered_pages", sa.JSON()), sa.column("submitted_review", sa.JSON()))
    for row in connection.execute(sa.select(states)).mappings():
        context = connection.scalar(sa.select(entries.c.context_json).where(entries.c.entry_id == row["run_id"])) or {}
        context["risk_delivered_pages"] = row["delivered_pages"]
        if row["submitted_review"] is not None:
            context["submitted_risk_review"] = row["submitted_review"]
        connection.execute(entries.update().where(entries.c.entry_id == row["run_id"]).values(context_json=context))
    op.drop_table("research_risk_read_state")
    from watchlist_migration_snapshots.research_read_context_0065 import derive_read_context as previous_projection
    entries = sa.table("research_entry", sa.column("entry_id", sa.String()),
        sa.column("context_json", sa.JSON()), sa.column("read_context_json", sa.JSON()))
    last_id = None
    while True:
        query = sa.select(entries.c.entry_id, entries.c.context_json).order_by(entries.c.entry_id).limit(1)
        if last_id is not None:
            query = query.where(entries.c.entry_id > last_id)
        row = connection.execute(query).first()
        if row is None:
            break
        last_id = row.entry_id
        connection.execute(entries.update().where(entries.c.entry_id == last_id)
            .values(read_context_json=previous_projection(row.context_json)))
