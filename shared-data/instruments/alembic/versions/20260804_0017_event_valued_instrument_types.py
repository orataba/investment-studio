"""Add FCN and option instrument categories.

Revision ID: 20260804_0017
Revises: 20260731_0016
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260804_0017"
down_revision = "20260731_0016"
branch_labels = None
depends_on = None


CHECK_CONSTRAINT_NAME = "instrument_type_contract"
PREVIOUS_TYPES = (
    "fund",
    "etf",
    "index",
    "bond",
    "equity",
    "cash",
    "fx",
    "other",
)
EVENT_VALUED_TYPES = ("fcn", "option")
CURRENT_TYPES = (*PREVIOUS_TYPES[:-3], *EVENT_VALUED_TYPES, *PREVIOUS_TYPES[-3:])


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
                "AND lower(sql) LIKE '%instrument%' "
                "ORDER BY name"
            )
        )
    ]


def _drop_sqlite_triggers(
    connection: sa.Connection,
    trigger_definitions: list[tuple[str, str]],
) -> None:
    for name, _sql in trigger_definitions:
        quoted_name = '"' + name.replace('"', '""') + '"'
        connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {quoted_name}"))


def _restore_sqlite_triggers(
    connection: sa.Connection,
    trigger_definitions: list[tuple[str, str]],
) -> None:
    for _name, sql in trigger_definitions:
        connection.execute(sa.text(sql))


def _replace_check_constraint(values: tuple[str, ...]) -> None:
    connection = op.get_bind()
    trigger_definitions = _sqlite_trigger_definitions(connection)
    _drop_sqlite_triggers(connection, trigger_definitions)
    with op.batch_alter_table("instrument") as batch_op:
        batch_op.drop_constraint(CHECK_CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(
            CHECK_CONSTRAINT_NAME,
            f"instrument_type IN ({_quoted(values)})",
        )
    _restore_sqlite_triggers(connection, trigger_definitions)


def upgrade() -> None:
    _replace_check_constraint(CURRENT_TYPES)


def downgrade() -> None:
    connection = op.get_bind()
    event_valued_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM instrument "
            "WHERE instrument_type IN ('fcn', 'option')"
        )
    )
    if int(event_valued_count or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade 20260804_0017 while FCN or option instruments exist."
        )
    _replace_check_constraint(PREVIOUS_TYPES)
