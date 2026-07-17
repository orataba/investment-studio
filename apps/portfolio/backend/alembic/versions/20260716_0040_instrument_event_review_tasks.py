"""Add auditable Portfolio review tasks for Registry instrument events.

Revision ID: 20260716_0040
Revises: 20260715_0039

The Registry remains the canonical event ledger.  These tables are a
Portfolio/account projection and immutable human-review history; they never
create cash or units on their own.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0040"
down_revision = "20260715_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_instrument_event_task",
        sa.Column("instrument_event_task_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("event_source", sa.String(), nullable=False),
        sa.Column("event_action_id", sa.String(), nullable=False),
        sa.Column("current_event_revision_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("source_revision_kind", sa.String(), nullable=False),
        sa.Column("source_event_state", sa.String(), nullable=False),
        sa.Column("announcement_date", sa.Date(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("payable_date", sa.Date(), nullable=True),
        sa.Column("cash_per_unit", sa.Numeric(28, 12), nullable=True),
        sa.Column("unit_ratio", sa.Numeric(28, 12), nullable=True),
        sa.Column("reinvestment_nav", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "entitled_quantity",
            sa.Numeric(28, 12),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "resolution_status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_event_revision_id", sa.String(), nullable=True),
        sa.Column("resolution_note", sa.String(), nullable=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.String(), nullable=True),
        sa.Column(
            "row_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "source_event_state IN ('active', 'cancelled')",
            name="ck_portfolio_instrument_event_task_source_state",
        ),
        sa.CheckConstraint(
            "source_revision_kind IN ('original', 'correction', 'cancellation')",
            name="ck_portfolio_instrument_event_task_revision_kind",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('pending', 'processed', 'not_applicable')",
            name="ck_portfolio_instrument_event_task_resolution_status",
        ),
        sa.CheckConstraint(
            "CAST(entitled_quantity AS NUMERIC) >= 0",
            name="ck_portfolio_instrument_event_task_entitled_quantity",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_portfolio_instrument_event_task_row_version",
        ),
        sa.CheckConstraint(
            "length(trim(event_source)) > 0 "
            "AND length(trim(event_action_id)) > 0 "
            "AND length(trim(current_event_revision_id)) > 0 "
            "AND length(trim(event_type)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0",
            name="ck_portfolio_instrument_event_task_identity",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account_record.account_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instrument_event_task_id"),
        sa.UniqueConstraint(
            "portfolio_id",
            "account_id",
            "event_source",
            "event_action_id",
            name="uq_portfolio_instrument_event_task_scope_action",
        ),
    )
    op.create_index(
        "ix_portfolio_instrument_event_task_attention",
        "portfolio_instrument_event_task",
        [
            "portfolio_id",
            "source_event_state",
            "resolution_status",
            "effective_date",
        ],
        unique=False,
    )
    op.create_index(
        "ix_portfolio_instrument_event_task_instrument",
        "portfolio_instrument_event_task",
        ["portfolio_id", "instrument_id", "effective_date"],
        unique=False,
    )

    op.create_table(
        "portfolio_instrument_event_task_link",
        sa.Column("instrument_event_task_link_id", sa.String(), nullable=False),
        sa.Column("instrument_event_task_id", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("link_role", sa.String(), nullable=False),
        sa.Column("linked_event_revision_id", sa.String(), nullable=False),
        sa.Column("linked_by", sa.String(), nullable=False),
        sa.Column("linked_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "link_role IN ('distribution', 'reinvestment', 'reinvestment_purchase')",
            name="ck_portfolio_instrument_event_task_link_role",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_event_task_id"],
            ["portfolio_instrument_event_task.instrument_event_task_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transaction_record.transaction_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instrument_event_task_link_id"),
        sa.UniqueConstraint(
            "instrument_event_task_id",
            "transaction_id",
            name="uq_portfolio_instrument_event_task_link_transaction",
        ),
    )
    op.create_index(
        "ix_portfolio_instrument_event_task_link_transaction",
        "portfolio_instrument_event_task_link",
        ["transaction_id", "instrument_event_task_id"],
        unique=False,
    )

    op.create_table(
        "portfolio_instrument_event_task_review",
        sa.Column("instrument_event_task_review_id", sa.String(), nullable=False),
        sa.Column("instrument_event_task_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("event_revision_id", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("linked_transaction_ids_json", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("reviewed_by", sa.String(), nullable=False),
        sa.Column("reviewed_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('processed', 'not_applicable', 'reopened')",
            name="ck_portfolio_instrument_event_task_review_decision",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_event_task_id"],
            ["portfolio_instrument_event_task.instrument_event_task_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instrument_event_task_review_id"),
    )
    op.create_index(
        "ix_portfolio_instrument_event_task_review_task_time",
        "portfolio_instrument_event_task_review",
        ["instrument_event_task_id", "reviewed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_portfolio_instrument_event_task_review_task_time",
        table_name="portfolio_instrument_event_task_review",
    )
    op.drop_table("portfolio_instrument_event_task_review")
    op.drop_index(
        "ix_portfolio_instrument_event_task_link_transaction",
        table_name="portfolio_instrument_event_task_link",
    )
    op.drop_table("portfolio_instrument_event_task_link")
    op.drop_index(
        "ix_portfolio_instrument_event_task_instrument",
        table_name="portfolio_instrument_event_task",
    )
    op.drop_index(
        "ix_portfolio_instrument_event_task_attention",
        table_name="portfolio_instrument_event_task",
    )
    op.drop_table("portfolio_instrument_event_task")
