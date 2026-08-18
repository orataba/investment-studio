"""Add the local FMP equity search catalog.

Revision ID: 20260818_0003
Revises: 20260716_0002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fmp_equity_catalog",
        sa.Column("fmp_symbol", sa.String(length=32), nullable=False),
        sa.Column("exchange_ticker", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=512), nullable=False),
        sa.Column("exchange_code", sa.String(length=4), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=256), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "exchange_code IN ('XNAS', 'XNYS', 'XASE', 'XHKG', 'XSHG', 'XSHE')",
            name="ck_fmp_equity_catalog_exchange_code_contract",
        ),
        sa.CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name="ck_fmp_equity_catalog_currency_contract",
        ),
        sa.PrimaryKeyConstraint("fmp_symbol", name="pk_fmp_equity_catalog"),
        sa.UniqueConstraint(
            "exchange_ticker",
            name="uq_fmp_equity_catalog_exchange_ticker",
        ),
    )
    op.create_index(
        "ix_fmp_equity_catalog_company_name",
        "fmp_equity_catalog",
        ["company_name"],
    )
    op.create_index(
        "ix_fmp_equity_catalog_exchange",
        "fmp_equity_catalog",
        ["exchange_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fmp_equity_catalog_exchange",
        table_name="fmp_equity_catalog",
    )
    op.drop_index(
        "ix_fmp_equity_catalog_company_name",
        table_name="fmp_equity_catalog",
    )
    op.drop_table("fmp_equity_catalog")
