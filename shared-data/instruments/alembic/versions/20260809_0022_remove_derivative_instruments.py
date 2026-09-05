"""Keep shared Registry limited to reusable market instruments.

FCNs and options are Portfolio-local contracts. This migration refuses to
discard any Registry derivative data; the operator must resolve it before the
schema is simplified.

Revision ID: 20260809_0022
Revises: 20260809_0021
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0022"
down_revision = "20260809_0021"
branch_labels = None
depends_on = None


INSTRUMENT_TYPES = (
    "fund",
    "etf",
    "index",
    "bond",
    "equity",
    "cash",
    "fx",
    "other",
)
DERIVATIVE_COLUMNS = (
    "option_underlying_instrument_id",
    "option_type",
    "option_expiry_date",
    "option_strike",
    "option_contract_multiplier",
    "option_settlement_type",
    "option_contract_currency",
    "fcn_contract_json",
    "derivative_adjustment_policy_json",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _sqlite_trigger_definitions(
    connection: sa.Connection,
) -> list[tuple[str, str]]:
    if connection.dialect.name != "sqlite":
        return []
    return [
        (str(row.name), str(row.sql))
        for row in connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND sql IS NOT NULL "
                "AND lower(sql) LIKE '%instrument%' ORDER BY name"
            )
        )
    ]


def _drop_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    for name, _sql in definitions:
        quoted_name = '"' + name.replace('"', '""') + '"'
        connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {quoted_name}"))


def _restore_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    for _name, sql in definitions:
        connection.execute(sa.text(sql))


def _preflight(connection: sa.Connection) -> None:
    derivative_count = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM instrument "
                "WHERE instrument_type IN ('fcn', 'option')"
            )
        )
        or 0
    )
    contract_identifier_count = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM instrument_broker_identifier "
                "WHERE identifier_type = 'contract_id'"
            )
        )
        or 0
    )
    if derivative_count or contract_identifier_count:
        raise RuntimeError(
            "Registry derivative removal requires zero FCN/option instruments "
            "and zero contract_id broker identifiers; found "
            f"{derivative_count} derivative instrument(s) and "
            f"{contract_identifier_count} contract identifier(s)."
        )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    _preflight(connection)

    trigger_definitions = _sqlite_trigger_definitions(connection)
    _drop_sqlite_triggers(connection, trigger_definitions)
    with op.batch_alter_table(
        "instrument",
        recreate="always" if connection.dialect.name == "sqlite" else "auto",
    ) as batch_op:
        batch_op.drop_index("ix_instrument_option_underlying")
        batch_op.drop_constraint(
            op.f("ck_instrument_derivative_adjustment_policy"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_instrument_fcn_contract_metadata"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_instrument_option_underlying_distinct"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_instrument_option_contract_identity"),
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_instrument_option_underlying_instrument_id_instrument",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "instrument_type_contract",
            type_="check",
        )
        for column_name in reversed(DERIVATIVE_COLUMNS):
            batch_op.drop_column(column_name)
        batch_op.create_check_constraint(
            "instrument_type_contract",
            f"instrument_type IN ({_quoted(INSTRUMENT_TYPES)})",
        )
    _restore_sqlite_triggers(connection, trigger_definitions)

    with op.batch_alter_table("instrument_broker_identifier") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_instrument_broker_identifier_broker_identifier_type"),
            type_="check",
        )
        batch_op.create_check_constraint(
            op.f("ck_instrument_broker_identifier_broker_identifier_type"),
            "identifier_type IN ('symbol', 'product_code')",
        )


def downgrade() -> None:
    raise RuntimeError(
        "Shared Registry no longer owns derivative contracts; restore the "
        "pre-migration database backup instead of recreating that model."
    )
