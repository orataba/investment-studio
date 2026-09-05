"""Expand FMP stock and ETF catalogs to the supported European exchanges.

Revision ID: 20260822_0006
Revises: 20260822_0005
"""

from __future__ import annotations

from alembic import op


revision = "20260822_0006"
down_revision = "20260822_0005"
branch_labels = None
depends_on = None


EQUITY_EXCHANGES = (
    "XNAS",
    "XNYS",
    "XASE",
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
ETF_EXCHANGES = (*EQUITY_EXCHANGES[:3], "BATS", *EQUITY_EXCHANGES[3:])


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    for table_name, exchanges in (
        ("fmp_equity_catalog", EQUITY_EXCHANGES),
        ("fmp_etf_catalog", ETF_EXCHANGES),
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                op.f(f"ck_{table_name}_exchange_code_contract"),
                type_="check",
            )
            batch_op.create_check_constraint(
                "exchange_code_contract",
                f"exchange_code IN ({_quoted(exchanges)})",
            )


def downgrade() -> None:
    raise RuntimeError(
        "European FMP catalog identities are production data; restore the "
        "pre-migration database backup instead of dropping their contracts."
    )
