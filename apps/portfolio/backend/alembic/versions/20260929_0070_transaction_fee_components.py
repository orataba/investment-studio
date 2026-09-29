"""Preserve independently categorized charges on one transaction."""
from decimal import Decimal

from alembic import op
import sqlalchemy as sa

revision = "20260929_0070"
down_revision = "20260924_0069"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("transaction_record", sa.Column("fee_components_json", sa.JSON(), nullable=True))
    table = sa.table("transaction_record",
        sa.column("transaction_id", sa.String()), sa.column("fees", sa.Float()),
        sa.column("source_fees", sa.Numeric(28, 8)), sa.column("fee_category", sa.String()),
        sa.column("fee_components_json", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table.c.transaction_id, table.c.fees, table.c.source_fees, table.c.fee_category).where(table.c.fees > 0)).mappings():
        amount = row["source_fees"] if row["source_fees"] is not None else Decimal(str(row["fees"]))
        connection.execute(table.update().where(table.c.transaction_id == row["transaction_id"]).values(
            fee_components_json=[{"category": row["fee_category"] or "unknown", "amount": str(amount)}],
        ))


def downgrade():
    raise RuntimeError("Restore the pre-migration database backup to remove transaction fee components without losing categorized fee evidence.")
