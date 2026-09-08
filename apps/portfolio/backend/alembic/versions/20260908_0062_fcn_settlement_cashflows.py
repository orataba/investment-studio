"""Keep final FCN coupon and contract expenses with their settlement fact."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0062"
down_revision = "20260908_0061"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("transaction_record", sa.Column("settlement_cashflows_json", sa.JSON(), nullable=True))


def downgrade():
    used = op.get_bind().execute(sa.text(
        "SELECT 1 FROM transaction_record WHERE CAST(settlement_cashflows_json AS TEXT) NOT IN ('[]', 'null') LIMIT 1"
    )).first()
    if used:
        raise RuntimeError("Cannot remove recorded FCN settlement cashflows; restore the pre-migration backup instead.")
    op.drop_column("transaction_record", "settlement_cashflows_json")
