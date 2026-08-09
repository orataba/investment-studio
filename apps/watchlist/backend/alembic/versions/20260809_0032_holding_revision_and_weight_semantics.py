"""Make holdings append-only and expose weighted-metric coverage.

Revision ID: 20260809_0032
Revises: 20260809_0031
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0032"
down_revision = "20260809_0031"
branch_labels = None
depends_on = None


def _set_holdings_cutoff_field(*, label: str, description: str) -> None:
    field_registry = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.Text()),
    )
    op.get_bind().execute(
        sa.update(field_registry)
        .where(field_registry.c.field_key == "exposure_updated_at")
        .values(label=label, description=description)
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    duplicate = bind.execute(
        sa.text(
            """
            SELECT instrument_id, input_hash, COUNT(*) AS row_count
            FROM holding_snapshot
            WHERE input_hash IS NOT NULL
            GROUP BY instrument_id, input_hash
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot make holding snapshots idempotent: duplicate "
            "(instrument_id, input_hash) rows exist for "
            f"instrument_id={duplicate['instrument_id']!r}."
        )

    with op.batch_alter_table("holding_snapshot") as batch_op:
        batch_op.create_unique_constraint(
            "uq_holding_snapshot_instrument_input_hash",
            ["instrument_id", "input_hash"],
        )
    with op.batch_alter_table("exposure_analytics_snapshot") as batch_op:
        batch_op.add_column(
            sa.Column("reported_weight_total", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("duration_weight_coverage", sa.Numeric(12, 6), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ytw_weight_coverage", sa.Numeric(12, 6), nullable=True)
        )
    _set_holdings_cutoff_field(
        label="Holdings Source Cutoff",
        description=(
            "Evidence cutoff timestamp of the holdings statement used by the "
            "current exposure snapshot."
        ),
    )


def downgrade() -> None:
    _set_holdings_cutoff_field(
        label="Holdings Updated At",
        description="Latest holdings statement adoption time.",
    )
    with op.batch_alter_table("exposure_analytics_snapshot") as batch_op:
        batch_op.drop_column("ytw_weight_coverage")
        batch_op.drop_column("duration_weight_coverage")
        batch_op.drop_column("reported_weight_total")
    with op.batch_alter_table("holding_snapshot") as batch_op:
        batch_op.drop_constraint(
            "uq_holding_snapshot_instrument_input_hash",
            type_="unique",
        )
