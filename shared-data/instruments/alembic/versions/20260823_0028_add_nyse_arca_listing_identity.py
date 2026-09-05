"""Add NYSE Arca to the canonical listed-instrument identity contract.

Revision ID: 20260823_0028
Revises: 20260823_0027
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_0028"
down_revision: str | None = "20260823_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LISTED_INSTRUMENT_TYPES = ("equity", "etf")
SUPPORTED_LISTING_EXCHANGES = (
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


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    connection = op.get_bind()
    trigger_definitions: list[str] = []
    if connection.dialect.name == "sqlite":
        trigger_definitions = [
            str(row.sql)
            for row in connection.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'trigger' "
                    "AND sql IS NOT NULL ORDER BY name"
                )
            )
        ]
        for row in connection.execute(
            sa.text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' ORDER BY name"
            )
        ):
            quoted_name = '"' + str(row.name).replace('"', '""') + '"'
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {quoted_name}")

    recreate = "always" if connection.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("instrument", recreate=recreate) as batch_op:
        batch_op.drop_constraint("instrument_exchange_contract", type_="check")
        batch_op.create_check_constraint(
            "instrument_exchange_contract",
            "((instrument_type IN "
            f"({_quoted(LISTED_INSTRUMENT_TYPES)}) AND exchange_code IS NOT NULL "
            "AND exchange_code IN "
            f"({_quoted(SUPPORTED_LISTING_EXCHANGES)})) OR "
            f"(instrument_type NOT IN ({_quoted(LISTED_INSTRUMENT_TYPES)}) "
            "AND exchange_code IS NULL))",
        )

    for definition in trigger_definitions:
        connection.exec_driver_sql(definition)


def downgrade() -> None:
    raise RuntimeError(
        "NYSE Arca listing identities are canonical data; restore the pre-migration "
        "database backup instead of narrowing the exchange contract."
    )
