"""Add durable recalc source-event inbox and coalesced invalidation generations.

Revision ID: 20260714_0034
Revises: 20260714_0033
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0034"
down_revision = "20260714_0033"
branch_labels = None
depends_on = None


def _assert_no_open_legacy_source_event_jobs(
    connection: sa.Connection,
) -> None:
    """Refuse to discard the delivery state of work that has not finished."""

    rows = connection.execute(
        sa.text(
            """
            SELECT recalc_job_id, job_status, trigger_ref_type, trigger_ref_id
            FROM recalc_job
            WHERE job_status IN ('queued', 'running')
              AND trigger_ref_type IS NOT NULL
              AND TRIM(trigger_ref_type) <> ''
              AND trigger_ref_id IS NOT NULL
              AND TRIM(trigger_ref_id) <> ''
            ORDER BY enqueued_at, recalc_job_id
            LIMIT 10
            """
        )
    ).mappings().all()
    if not rows:
        return
    examples = ", ".join(
        f"{row['recalc_job_id']}({row['job_status']})" for row in rows
    )
    raise RuntimeError(
        "Cannot introduce the forward-only recalc source-event inbox while "
        "legacy source-referenced jobs are still queued or running. Drain or "
        f"cancel them before retrying the migration. Examples: {examples}."
    )


def upgrade() -> None:
    # Historical terminal jobs are execution audit, not a reliable delivery
    # ledger.  Start the inbox forward-only rather than manufacturing pending
    # invalidations from completed maintenance/rebuild executions.  Open
    # source-referenced jobs are the only legacy rows whose delivery state
    # would otherwise be lost, so fail before making any schema change.
    _assert_no_open_legacy_source_event_jobs(op.get_bind())

    op.add_column(
        "recalc_job",
        sa.Column("claimed_generation", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("recalc_job") as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_recalc_job_claimed_generation"),
            "claimed_generation IS NULL OR claimed_generation > 0",
        )

    op.create_table(
        "recalc_source_event_inbox",
        sa.Column("source_event_inbox_id", sa.String(), nullable=False),
        sa.Column("trigger_ref_type", sa.String(), nullable=False),
        sa.Column("trigger_ref_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "source_event_inbox_id",
            name=op.f("pk_recalc_source_event_inbox"),
        ),
        sa.UniqueConstraint(
            "trigger_ref_type",
            "trigger_ref_id",
            "instrument_id",
            "job_type",
            name="uq_recalc_source_event_inbox_identity",
        ),
        sa.CheckConstraint(
            "TRIM(trigger_ref_type) <> '' AND TRIM(trigger_ref_id) <> '' "
            "AND TRIM(instrument_id) <> '' AND TRIM(job_type) <> ''",
            name=op.f("ck_recalc_source_event_inbox_identity"),
        ),
        sa.CheckConstraint(
            "disposition IN ('recalc', 'ignored')",
            name=op.f("ck_recalc_source_event_inbox_disposition"),
        ),
        sa.CheckConstraint(
            "(disposition = 'recalc' AND generation IS NOT NULL "
            "AND generation > 0) OR "
            "(disposition = 'ignored' AND generation IS NULL "
            "AND consumed_at IS NOT NULL)",
            name=op.f("ck_recalc_source_event_inbox_lifecycle"),
        ),
    )
    op.create_index(
        "idx_recalc_source_event_inbox_pending",
        "recalc_source_event_inbox",
        ["instrument_id", "job_type", "consumed_at", "generation"],
        unique=False,
    )

    op.create_table(
        "recalc_invalidation_state",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column(
            "requested_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "completed_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            name=op.f(
                "fk_recalc_invalidation_state_instrument_id_instrument_detail"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id",
            "job_type",
            name=op.f("pk_recalc_invalidation_state"),
        ),
        sa.CheckConstraint(
            "requested_generation >= 0 AND completed_generation >= 0 "
            "AND completed_generation <= requested_generation",
            name=op.f("ck_recalc_invalidation_state_generation_order"),
        ),
    )
    op.create_index(
        "idx_recalc_invalidation_state_pending",
        "recalc_invalidation_state",
        ["requested_generation", "completed_generation"],
        unique=False,
    )

def downgrade() -> None:
    op.drop_index(
        "idx_recalc_invalidation_state_pending",
        table_name="recalc_invalidation_state",
    )
    op.drop_table("recalc_invalidation_state")
    op.drop_index(
        "idx_recalc_source_event_inbox_pending",
        table_name="recalc_source_event_inbox",
    )
    op.drop_table("recalc_source_event_inbox")
    with op.batch_alter_table("recalc_job") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_recalc_job_claimed_generation"),
            type_="check",
        )
        batch_op.drop_column("claimed_generation")
