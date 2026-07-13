"""Require explicit ISO-style currency codes on canonical instruments and series.

Revision ID: 20260713_0011
Revises: 20260713_0010
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0011"
down_revision = "20260713_0010"
branch_labels = None
depends_on = None


CURRENCY_CODE_CHECK = (
    "length(currency) = 3 "
    "AND substr(currency, 1, 1) BETWEEN 'A' AND 'Z' "
    "AND substr(currency, 2, 1) BETWEEN 'A' AND 'Z' "
    "AND substr(currency, 3, 1) BETWEEN 'A' AND 'Z'"
)


def _invalid_currency_count(connection: sa.Connection, table_name: str) -> int:
    table = sa.table(table_name, sa.column("currency", sa.String()))
    return int(
        connection.scalar(
            sa.select(sa.func.count())
            .select_from(table)
            .where(
                sa.or_(
                    sa.func.length(table.c.currency) != 3,
                    sa.not_(sa.func.substr(table.c.currency, 1, 1).between("A", "Z")),
                    sa.not_(sa.func.substr(table.c.currency, 2, 1).between("A", "Z")),
                    sa.not_(sa.func.substr(table.c.currency, 3, 1).between("A", "Z")),
                )
            )
        )
        or 0
    )


def _check_names(connection: sa.Connection, table_name: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(connection).get_check_constraints(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    connection = op.get_bind()
    invalid_instruments = _invalid_currency_count(connection, "instrument")
    invalid_series = _invalid_currency_count(connection, "quote_series")
    if invalid_instruments or invalid_series:
        raise RuntimeError(
            "Currency-code constraint migration requires explicit three-letter "
            "uppercase facts before DDL: "
            f"instrument={invalid_instruments}, quote_series={invalid_series}."
        )

    instrument_checks = _check_names(connection, "instrument")
    if "ck_instrument_currency_iso_code" not in instrument_checks:
        with op.batch_alter_table("instrument") as batch_op:
            batch_op.create_check_constraint(
                op.f("ck_instrument_currency_iso_code"),
                CURRENCY_CODE_CHECK,
            )

    quote_series_checks = _check_names(connection, "quote_series")
    with op.batch_alter_table("quote_series") as batch_op:
        for old_name in ("currency", "ck_quote_series_currency"):
            if old_name in quote_series_checks:
                batch_op.drop_constraint(op.f(old_name), type_="check")
        if "ck_quote_series_currency_iso_code" not in quote_series_checks:
            batch_op.create_check_constraint(
                op.f("ck_quote_series_currency_iso_code"),
                CURRENCY_CODE_CHECK,
            )


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0011 is irreversible: weakening canonical currency identity "
        "would re-open silent denomination ambiguity."
    )
