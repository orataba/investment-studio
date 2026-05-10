"""portfolio kernel foundation

Revision ID: 20260415_0001
Revises:
Create Date: 2026-04-15 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260415_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_record",
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("portfolio_name", sa.String(), nullable=False),
        sa.Column("base_currency", sa.String(), nullable=False),
        sa.Column("valuation_timezone", sa.String(), nullable=False),
        sa.Column("valuation_cutoff_policy", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("nav", sa.Float(), nullable=False),
        sa.Column("day_change_value", sa.Float(), nullable=False),
        sa.Column("day_change_pct", sa.Float(), nullable=False),
        sa.Column("securities_count", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("portfolio_id", name=op.f("pk_portfolio_record")),
    )
    op.create_table(
        "account_record",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("account_name", sa.String(), nullable=False),
        sa.Column("account_type", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("default_settlement_cash_account_id", sa.String(), nullable=True),
        sa.Column("cost_basis_method", sa.String(), nullable=True),
        sa.Column("allowed_instrument_types_json", sa.JSON(), nullable=True),
        sa.Column("opened_at", sa.Date(), nullable=True),
        sa.Column("closed_at", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            name=op.f("fk_account_record_portfolio_id_portfolio_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_account_record")),
    )
    op.create_table(
        "transaction_record",
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("trade_time", sa.String(), nullable=False),
        sa.Column("trade_at", sa.String(), nullable=False),
        sa.Column("trade_timezone", sa.String(), nullable=False),
        sa.Column("trade_time_is_estimated", sa.Boolean(), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("entitlement_date", sa.Date(), nullable=True),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("settlement_cash_account_id", sa.String(), nullable=True),
        sa.Column("instrument_id", sa.String(), nullable=True),
        sa.Column("instrument_ref_json", sa.JSON(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("gross_amount", sa.Float(), nullable=False),
        sa.Column("counter_amount", sa.Float(), nullable=True),
        sa.Column("fx_rate", sa.Float(), nullable=True),
        sa.Column("fees", sa.Float(), nullable=False),
        sa.Column("taxes", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("transfer_scope", sa.String(), nullable=True),
        sa.Column("transfer_object_type", sa.String(), nullable=True),
        sa.Column("transfer_group_id", sa.String(), nullable=True),
        sa.Column("counterparty_account_id", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            name=op.f("fk_transaction_record_portfolio_id_portfolio_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("transaction_id", name=op.f("pk_transaction_record")),
    )
    op.create_table(
        "taxonomy_record",
        sa.Column("taxonomy_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("taxonomy_type", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=True),
        sa.Column("primary_assignment_scope", sa.String(), nullable=False),
        sa.Column("planning_enabled", sa.Boolean(), nullable=False),
        sa.Column("budgeting_level", sa.String(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_template_ref", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            name=op.f("fk_taxonomy_record_portfolio_id_portfolio_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("taxonomy_id", name=op.f("pk_taxonomy_record")),
    )
    op.create_table(
        "taxonomy_node_record",
        sa.Column("taxonomy_node_id", sa.String(), nullable=False),
        sa.Column("taxonomy_id", sa.String(), nullable=False),
        sa.Column("parent_taxonomy_node_id", sa.String(), nullable=True),
        sa.Column("node_name", sa.String(), nullable=False),
        sa.Column("node_code", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_terminal", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["taxonomy_id"],
            ["taxonomy_record.taxonomy_id"],
            name=op.f("fk_taxonomy_node_record_taxonomy_id_taxonomy_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("taxonomy_node_id", name=op.f("pk_taxonomy_node_record")),
    )
    op.create_table(
        "taxonomy_assignment_record",
        sa.Column("assignment_id", sa.String(), nullable=False),
        sa.Column("taxonomy_id", sa.String(), nullable=False),
        sa.Column("target_scope", sa.String(), nullable=False),
        sa.Column("target_entity_id", sa.String(), nullable=False),
        sa.Column("taxonomy_node_id", sa.String(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["taxonomy_id"],
            ["taxonomy_record.taxonomy_id"],
            name=op.f("fk_taxonomy_assignment_record_taxonomy_id_taxonomy_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assignment_id", name=op.f("pk_taxonomy_assignment_record")),
    )


def downgrade() -> None:
    op.drop_table("taxonomy_assignment_record")
    op.drop_table("taxonomy_node_record")
    op.drop_table("taxonomy_record")
    op.drop_table("transaction_record")
    op.drop_table("account_record")
    op.drop_table("portfolio_record")
