"""Require the minimum option contract identity used by lifecycle validation.

Existing option rows cannot be upgraded without an explicit identity backfill.

Revision ID: 20260806_0018
Revises: 20260804_0017
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260806_0018"
down_revision = "20260804_0017"
branch_labels = None
depends_on = None


IDENTITY_COLUMNS = (
    "option_underlying_instrument_id",
    "option_type",
    "option_expiry_date",
    "option_strike",
    "option_contract_multiplier",
    "option_settlement_type",
    "option_contract_currency",
)


def _sqlite_trigger_definitions(connection: sa.Connection) -> list[tuple[str, str]]:
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


def _drop_sqlite_triggers(connection: sa.Connection, definitions: list[tuple[str, str]]) -> None:
    for name, _sql in definitions:
        quoted = '"' + name.replace('"', '""') + '"'
        connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {quoted}"))


def _restore_sqlite_triggers(connection: sa.Connection, definitions: list[tuple[str, str]]) -> None:
    for _name, sql in definitions:
        connection.execute(sa.text(sql))


def _columns(*, include_foreign_key: bool) -> tuple[sa.Column, ...]:
    return (
        sa.Column(
            "option_underlying_instrument_id",
            sa.String(),
            *(
                (sa.ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),)
                if include_foreign_key
                else ()
            ),
            nullable=True,
        ),
        sa.Column("option_type", sa.String(), nullable=True),
        sa.Column("option_expiry_date", sa.Date(), nullable=True),
        sa.Column("option_strike", sa.Numeric(28, 12), nullable=True),
        sa.Column("option_contract_multiplier", sa.Numeric(28, 12), nullable=True),
        sa.Column("option_settlement_type", sa.String(), nullable=True),
        sa.Column("option_contract_currency", sa.String(), nullable=True),
    )


IDENTITY_CONDITION = (
    "((instrument_type <> 'option' AND "
    "option_underlying_instrument_id IS NULL AND option_type IS NULL AND "
    "option_expiry_date IS NULL AND option_strike IS NULL AND "
    "option_contract_multiplier IS NULL AND option_settlement_type IS NULL AND "
    "option_contract_currency IS NULL) OR "
    "(instrument_type = 'option' AND "
    "option_underlying_instrument_id IS NOT NULL AND option_type IS NOT NULL AND "
    "option_type IN ('call','put') AND option_expiry_date IS NOT NULL AND "
    "option_strike IS NOT NULL AND option_strike > 0 AND "
    "option_contract_multiplier IS NOT NULL AND option_contract_multiplier > 0 AND "
    "option_settlement_type IS NOT NULL AND option_settlement_type IN ('physical','cash') AND "
    "option_contract_currency IS NOT NULL AND option_contract_currency = currency AND "
    "option_contract_currency = upper(trim(option_contract_currency)) AND "
    "length(option_contract_currency) BETWEEN 1 AND 8))"
)


def upgrade() -> None:
    connection = op.get_bind()
    option_count = connection.scalar(
        sa.text("SELECT count(*) FROM instrument WHERE instrument_type = 'option'")
    )
    if int(option_count or 0) > 0:
        raise RuntimeError(
            "Cannot upgrade 20260806_0018 while identity-less option instruments exist; "
            "backfill or remove those rows before retrying."
        )

    if connection.dialect.name == "sqlite":
        trigger_definitions = _sqlite_trigger_definitions(connection)
        _drop_sqlite_triggers(connection, trigger_definitions)
        with op.batch_alter_table("instrument", recreate="always") as batch_op:
            for column in _columns(include_foreign_key=True):
                batch_op.add_column(column)
            batch_op.create_check_constraint(
                op.f("ck_instrument_option_contract_identity"),
                IDENTITY_CONDITION,
            )
            batch_op.create_check_constraint(
                op.f("ck_instrument_option_underlying_distinct"),
                "option_underlying_instrument_id IS NULL OR "
                "option_underlying_instrument_id <> instrument_id",
            )
            batch_op.create_index(
                "ix_instrument_option_underlying",
                ["option_underlying_instrument_id"],
            )
        _restore_sqlite_triggers(connection, trigger_definitions)
        return

    for column in _columns(include_foreign_key=False):
        op.add_column("instrument", column)
    op.create_foreign_key(
        "fk_instrument_option_underlying_instrument_id_instrument",
        "instrument",
        "instrument",
        ["option_underlying_instrument_id"],
        ["instrument_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_instrument_option_contract_identity"),
        "instrument",
        IDENTITY_CONDITION,
    )
    op.create_check_constraint(
        op.f("ck_instrument_option_underlying_distinct"),
        "instrument",
        "option_underlying_instrument_id IS NULL OR "
        "option_underlying_instrument_id <> instrument_id",
    )
    op.create_index(
        "ix_instrument_option_underlying",
        "instrument",
        ["option_underlying_instrument_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    identity_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM instrument WHERE "
            + " OR ".join(f"{column} IS NOT NULL" for column in IDENTITY_COLUMNS)
        )
    )
    if int(identity_count or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade 20260806_0018 while option contract identity exists."
        )

    if connection.dialect.name == "sqlite":
        trigger_definitions = _sqlite_trigger_definitions(connection)
        _drop_sqlite_triggers(connection, trigger_definitions)
        with op.batch_alter_table("instrument", recreate="always") as batch_op:
            batch_op.drop_index("ix_instrument_option_underlying")
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
            for column in reversed(IDENTITY_COLUMNS):
                batch_op.drop_column(column)
        _restore_sqlite_triggers(connection, trigger_definitions)
        return

    op.drop_index("ix_instrument_option_underlying", table_name="instrument")
    op.drop_constraint(
        op.f("ck_instrument_option_underlying_distinct"),
        "instrument",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_instrument_option_contract_identity"),
        "instrument",
        type_="check",
    )
    op.drop_constraint(
        "fk_instrument_option_underlying_instrument_id_instrument",
        "instrument",
        type_="foreignkey",
    )
    for column in reversed(IDENTITY_COLUMNS):
        op.drop_column("instrument", column)
