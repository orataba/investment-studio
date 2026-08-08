"""Add structured lifecycle and external source identity to transactions.

Revision ID: 20260804_0042
Revises: 20260728_0041
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260804_0042"
down_revision = "20260728_0041"
branch_labels = None
depends_on = None


UNIQUE_CONSTRAINT_NAME = "uq_transaction_record_portfolio_source_external"
SOURCE_CHECK_NAME = "source_identity"
LIFECYCLE_CHECK_NAME = "lifecycle_event_type"
EVENT_GROUP_INDEX = "ix_transaction_record_portfolio_event_group"
RELATED_INSTRUMENT_INDEX = "ix_transaction_record_portfolio_related_instrument"


def upgrade() -> None:
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.add_column(sa.Column("lifecycle_event_type", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("source_system", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("external_reference", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("event_group_id", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("related_instrument_id", sa.String(length=200), nullable=True))
        batch_op.create_unique_constraint(
            UNIQUE_CONSTRAINT_NAME,
            ["portfolio_id", "source_system", "external_reference"],
        )
        batch_op.create_check_constraint(
            SOURCE_CHECK_NAME,
            "external_reference IS NULL OR source_system IS NOT NULL",
        )
        batch_op.create_check_constraint(
            LIFECYCLE_CHECK_NAME,
            "lifecycle_event_type IS NULL OR lifecycle_event_type IN ("
            "'fcn_knock_in', 'fcn_knock_out', 'fcn_maturity', "
            "'fcn_physical_settlement', 'option_long_expiry', "
            "'option_long_exercise', 'option_writer_expiry', "
            "'option_assignment')",
        )
        batch_op.create_index(EVENT_GROUP_INDEX, ["portfolio_id", "event_group_id"])
        batch_op.create_index(
            RELATED_INSTRUMENT_INDEX,
            ["portfolio_id", "related_instrument_id"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    populated_count = int(
        connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM transaction_record WHERE "
                "lifecycle_event_type IS NOT NULL OR source_system IS NOT NULL OR "
                "external_reference IS NOT NULL OR event_group_id IS NOT NULL OR "
                "related_instrument_id IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    if populated_count:
        raise RuntimeError(
            "Cannot downgrade 20260804_0042 while lifecycle or external-source "
            "transaction identity is populated."
        )
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_index(RELATED_INSTRUMENT_INDEX)
        batch_op.drop_index(EVENT_GROUP_INDEX)
        batch_op.drop_constraint(LIFECYCLE_CHECK_NAME, type_="check")
        batch_op.drop_constraint(SOURCE_CHECK_NAME, type_="check")
        batch_op.drop_constraint(UNIQUE_CONSTRAINT_NAME, type_="unique")
        batch_op.drop_column("related_instrument_id")
        batch_op.drop_column("event_group_id")
        batch_op.drop_column("external_reference")
        batch_op.drop_column("source_system")
        batch_op.drop_column("lifecycle_event_type")
