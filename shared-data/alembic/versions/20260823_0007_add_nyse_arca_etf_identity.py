"""Add NYSE Arca to the FMP ETF catalog identity contract.

Revision ID: 20260823_0007
Revises: 20260822_0006
"""

from __future__ import annotations

from alembic import op


revision = "20260823_0007"
down_revision = "20260822_0006"
branch_labels = None
depends_on = None


ETF_EXCHANGES = (
    "XNAS",
    "XNYS",
    "XASE",
    "ARCX",
    "BATS",
    "XHKG",
    "XSHG",
    "XSHE",
    "XLON",
    "XETR",
    "XPAR",
    "XAMS",
    "XMIL",
    "XSWX",
)


def upgrade() -> None:
    with op.batch_alter_table("fmp_etf_catalog") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_fmp_etf_catalog_exchange_code_contract"),
            type_="check",
        )
        batch_op.create_check_constraint(
            "exchange_code_contract",
            "exchange_code IN ("
            + ", ".join(f"'{value}'" for value in ETF_EXCHANGES)
            + ")",
        )


def downgrade() -> None:
    raise RuntimeError(
        "NYSE Arca ETF identities are canonical data; restore the pre-migration "
        "database backup instead of narrowing the exchange contract."
    )
