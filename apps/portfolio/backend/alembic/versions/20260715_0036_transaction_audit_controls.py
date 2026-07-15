"""Add transaction audit, idempotency, and optimistic version controls.

Revision ID: 20260715_0036
Revises: 20260715_0035

Existing transaction facts are preserved and receive row_version 1.  The new
audit/idempotency tables start empty; no ledger facts are rebuilt or rewritten.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0036"
down_revision = "20260715_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transaction_record",
        sa.Column(
            "row_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_table(
        "transaction_change_log",
        sa.Column("change_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("change_type", sa.String(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("request_idempotency_key", sa.String(), nullable=True),
        sa.Column("changed_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "change_type IN ('create', 'update', 'delete')",
            name="ck_transaction_change_log_type",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("change_id"),
    )
    op.create_index(
        "ix_transaction_change_log_portfolio_transaction_changed",
        "transaction_change_log",
        ["portfolio_id", "transaction_id", "changed_at"],
        unique=False,
    )
    op.create_table(
        "transaction_idempotency_record",
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("transaction_ids_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("portfolio_id", "idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("transaction_idempotency_record")
    op.drop_index(
        "ix_transaction_change_log_portfolio_transaction_changed",
        table_name="transaction_change_log",
    )
    op.drop_table("transaction_change_log")
    op.drop_column("transaction_record", "row_version")
