"""portfolio fact indexes

Revision ID: 20260416_0002
Revises: 20260415_0001
Create Date: 2026-04-16 00:02:00
"""

from __future__ import annotations

from alembic import op


revision = "20260416_0002"
down_revision = "20260415_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_account_record_portfolio_type_currency",
        "account_record",
        ["portfolio_id", "account_type", "currency"],
        unique=False,
    )
    op.create_index(
        "ix_account_record_portfolio_name",
        "account_record",
        ["portfolio_id", "account_name"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_record_portfolio_trade_sort",
        "transaction_record",
        ["portfolio_id", "trade_date", "trade_at", "created_at", "transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_record_portfolio_account_trade",
        "transaction_record",
        ["portfolio_id", "account_id", "trade_date", "trade_at"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_record_portfolio_counterparty_trade",
        "transaction_record",
        ["portfolio_id", "counterparty_account_id", "trade_date", "trade_at"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_record_portfolio_type_trade",
        "transaction_record",
        ["portfolio_id", "transaction_type", "trade_date", "trade_at"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_record_portfolio_instrument_trade",
        "transaction_record",
        ["portfolio_id", "instrument_id", "trade_date", "trade_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_record_portfolio_instrument_trade", table_name="transaction_record")
    op.drop_index("ix_transaction_record_portfolio_type_trade", table_name="transaction_record")
    op.drop_index("ix_transaction_record_portfolio_counterparty_trade", table_name="transaction_record")
    op.drop_index("ix_transaction_record_portfolio_account_trade", table_name="transaction_record")
    op.drop_index("ix_transaction_record_portfolio_trade_sort", table_name="transaction_record")
    op.drop_index("ix_account_record_portfolio_name", table_name="account_record")
    op.drop_index("ix_account_record_portfolio_type_currency", table_name="account_record")
