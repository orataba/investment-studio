"""Preserve PM identity and structured research context in note history."""

from alembic import op
import sqlalchemy as sa

revision = "20260908_0055"
down_revision = "20260905_0054"
branch_labels = depends_on = None

TABLES = ("instrument_research_note", "instrument_research_note_revision")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("owner_user_id", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column("research_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        notes = sa.table(table, sa.column("owner_user_id", sa.Text()), sa.column("research_context", sa.JSON()))
        populated = connection.execute(
            sa.select(notes.c.owner_user_id).where(
                sa.or_(
                    notes.c.owner_user_id.is_not(None),
                    sa.cast(notes.c.research_context, sa.Text()) != "{}",
                )
            ).limit(1)
        ).first()
        if populated is not None:
            raise RuntimeError("Cannot downgrade: research-note identity or context has been recorded")
    for table in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("research_context")
            batch.drop_column("owner_user_id")
