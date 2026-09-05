"""Add FCN metadata, derivative adjustment policy, and broker reconciliation identity.

Existing derivative rows cannot be upgraded without explicit contract governance.

Revision ID: 20260807_0019
Revises: 20260806_0018
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260807_0019"
down_revision = "20260806_0018"
branch_labels = None
depends_on = None


FCN_CONTRACT_CONDITION = (
    "((instrument_type = 'fcn' AND fcn_contract_json IS NOT NULL AND "
    "lower(trim(CAST(fcn_contract_json AS TEXT))) NOT IN ('null','{}')) OR "
    "(instrument_type <> 'fcn' AND fcn_contract_json IS NULL))"
)
ADJUSTMENT_POLICY_CONDITION = (
    "((instrument_type IN ('fcn','option') AND "
    "derivative_adjustment_policy_json IS NOT NULL AND "
    "lower(trim(CAST(derivative_adjustment_policy_json AS TEXT))) "
    "NOT IN ('null','{}')) OR "
    "(instrument_type NOT IN ('fcn','option') AND "
    "derivative_adjustment_policy_json IS NULL))"
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


def _drop_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    for name, _sql in definitions:
        quoted = '"' + name.replace('"', '""') + '"'
        connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {quoted}"))


def _restore_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    for _name, sql in definitions:
        connection.execute(sa.text(sql))


def _create_broker_identifier_table() -> None:
    op.create_table(
        "instrument_broker_identifier",
        sa.Column(
            "instrument_broker_identifier_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "instrument_id",
            sa.String(),
            sa.ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("broker", sa.String(), nullable=False),
        sa.Column("identifier_type", sa.String(), nullable=False),
        sa.Column("identifier_value", sa.String(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "identifier_type IN ('contract_id','symbol','product_code')",
            name=op.f("ck_instrument_broker_identifier_broker_identifier_type"),
        ),
        sa.UniqueConstraint(
            "broker",
            "identifier_type",
            "identifier_value",
            name="uq_instrument_broker_identifier_identity",
        ),
    )
    op.create_index(
        "ix_instrument_broker_identifier_instrument",
        "instrument_broker_identifier",
        ["instrument_id", "broker"],
    )


def upgrade() -> None:
    connection = op.get_bind()
    derivative_count = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM instrument "
                "WHERE instrument_type IN ('fcn','option')"
            )
        )
        or 0
    )
    if derivative_count:
        raise RuntimeError(
            "Cannot upgrade 20260807_0019 while derivative instruments lack "
            "explicit FCN metadata or corporate-action adjustment policy; "
            "backfill through a controlled migration or remove those rows first."
        )

    if connection.dialect.name == "sqlite":
        trigger_definitions = _sqlite_trigger_definitions(connection)
        _drop_sqlite_triggers(connection, trigger_definitions)
        with op.batch_alter_table("instrument", recreate="always") as batch_op:
            batch_op.add_column(
                sa.Column("fcn_contract_json", sa.JSON(none_as_null=True))
            )
            batch_op.add_column(
                sa.Column(
                    "derivative_adjustment_policy_json",
                    sa.JSON(none_as_null=True),
                )
            )
            batch_op.create_check_constraint(
                op.f("ck_instrument_fcn_contract_metadata"),
                FCN_CONTRACT_CONDITION,
            )
            batch_op.create_check_constraint(
                op.f("ck_instrument_derivative_adjustment_policy"),
                ADJUSTMENT_POLICY_CONDITION,
            )
        _restore_sqlite_triggers(connection, trigger_definitions)
    else:
        op.add_column(
            "instrument",
            sa.Column("fcn_contract_json", sa.JSON(none_as_null=True)),
        )
        op.add_column(
            "instrument",
            sa.Column(
                "derivative_adjustment_policy_json",
                sa.JSON(none_as_null=True),
            ),
        )
        op.create_check_constraint(
            op.f("ck_instrument_fcn_contract_metadata"),
            "instrument",
            FCN_CONTRACT_CONDITION,
        )
        op.create_check_constraint(
            op.f("ck_instrument_derivative_adjustment_policy"),
            "instrument",
            ADJUSTMENT_POLICY_CONDITION,
        )

    _create_broker_identifier_table()


def downgrade() -> None:
    connection = op.get_bind()
    broker_identity_count = int(
        connection.scalar(
            sa.text("SELECT count(*) FROM instrument_broker_identifier")
        )
        or 0
    )
    derivative_metadata_count = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM instrument WHERE "
                "fcn_contract_json IS NOT NULL OR "
                "derivative_adjustment_policy_json IS NOT NULL"
            )
        )
        or 0
    )
    if broker_identity_count or derivative_metadata_count:
        raise RuntimeError(
            "Cannot downgrade 20260807_0019 while broker reconciliation identity "
            "or derivative contract governance metadata exists."
        )

    op.drop_index(
        "ix_instrument_broker_identifier_instrument",
        table_name="instrument_broker_identifier",
    )
    op.drop_table("instrument_broker_identifier")

    if connection.dialect.name == "sqlite":
        trigger_definitions = _sqlite_trigger_definitions(connection)
        _drop_sqlite_triggers(connection, trigger_definitions)
        with op.batch_alter_table("instrument", recreate="always") as batch_op:
            batch_op.drop_constraint(
                op.f("ck_instrument_derivative_adjustment_policy"),
                type_="check",
            )
            batch_op.drop_constraint(
                op.f("ck_instrument_fcn_contract_metadata"),
                type_="check",
            )
            batch_op.drop_column("derivative_adjustment_policy_json")
            batch_op.drop_column("fcn_contract_json")
        _restore_sqlite_triggers(connection, trigger_definitions)
        return

    op.drop_constraint(
        op.f("ck_instrument_derivative_adjustment_policy"),
        "instrument",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_instrument_fcn_contract_metadata"),
        "instrument",
        type_="check",
    )
    op.drop_column("instrument", "derivative_adjustment_policy_json")
    op.drop_column("instrument", "fcn_contract_json")
