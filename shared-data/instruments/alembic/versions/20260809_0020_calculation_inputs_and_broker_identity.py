"""Track calculation-setting changes and normalize broker identity.

Revision ID: 20260809_0020
Revises: 20260807_0019
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0020"
down_revision = "20260807_0019"
branch_labels = None
depends_on = None


BROKER_NORMALIZED_CHECK = (
    "broker = lower(trim(broker)) AND "
    "identifier_type = lower(trim(identifier_type)) AND "
    "identifier_value = lower(trim(identifier_value))"
)


def _normalize_broker_identifiers() -> None:
    bind = op.get_bind()
    broker_identifier = sa.table(
        "instrument_broker_identifier",
        sa.column("instrument_broker_identifier_id", sa.Integer()),
        sa.column("instrument_id", sa.String()),
        sa.column("broker", sa.String()),
        sa.column("identifier_type", sa.String()),
        sa.column("identifier_value", sa.String()),
    )
    rows = bind.execute(sa.select(broker_identifier)).mappings().all()
    normalized: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in rows:
        key = (
            str(row["broker"]).strip().casefold(),
            str(row["identifier_type"]).strip().lower(),
            str(row["identifier_value"]).strip().casefold(),
        )
        normalized.setdefault(key, []).append(dict(row))
    collisions = {
        key: values for key, values in normalized.items() if len(values) > 1
    }
    if collisions:
        details = "; ".join(
            f"{key} -> {sorted(str(item['instrument_id']) for item in values)}"
            for key, values in sorted(collisions.items())
        )
        raise RuntimeError(
            "Cannot normalize ambiguous broker identifiers. Resolve these identities first: "
            + details
        )
    for key, values in normalized.items():
        row = values[0]
        bind.execute(
            sa.update(broker_identifier)
            .where(
                broker_identifier.c.instrument_broker_identifier_id
                == row["instrument_broker_identifier_id"]
            )
            .values(
                broker=key[0],
                identifier_type=key[1],
                identifier_value=key[2],
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    op.add_column(
        "instrument",
        sa.Column("calculation_inputs_updated_at", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE instrument SET calculation_inputs_updated_at = market_data_updated_at"
    )
    _normalize_broker_identifiers()
    with op.batch_alter_table("instrument_broker_identifier") as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_instrument_broker_identifier_broker_identifier_normalized"),
            BROKER_NORMALIZED_CHECK,
        )


def downgrade() -> None:
    with op.batch_alter_table("instrument_broker_identifier") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_instrument_broker_identifier_broker_identifier_normalized"),
            type_="check",
        )
    op.drop_column("instrument", "calculation_inputs_updated_at")
