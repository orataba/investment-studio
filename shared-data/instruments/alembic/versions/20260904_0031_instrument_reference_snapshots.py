"""Persist collected asset reference data for read-only business pages."""

from alembic import op
import sqlalchemy as sa

revision = "20260904_0031"
down_revision = "20260904_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_reference_snapshot",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"],
                                name="fk_instrument_reference_snapshot_instrument_id_instrument",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instrument_id", name="pk_instrument_reference_snapshot"),
    )


def downgrade() -> None:
    op.drop_table("instrument_reference_snapshot")
