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


def upgrade() -> None:
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

    # Preserve source-event audit rows from 0032/0033. Their existing job ids are
    # stable inbox ids, and generation order follows the original enqueue order.
    op.execute(
        sa.text(
            """
            INSERT INTO recalc_source_event_inbox (
                source_event_inbox_id,
                trigger_ref_type,
                trigger_ref_id,
                instrument_id,
                job_type,
                disposition,
                generation,
                received_at,
                consumed_at
            )
            SELECT
                ranked.recalc_job_id,
                ranked.trigger_ref_type,
                ranked.trigger_ref_id,
                ranked.instrument_id,
                ranked.job_type,
                'recalc',
                ranked.generation,
                ranked.enqueued_at,
                NULL
            FROM (
                SELECT
                    recalc_job_id,
                    trigger_ref_type,
                    trigger_ref_id,
                    instrument_id,
                    job_type,
                    enqueued_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY instrument_id, job_type
                        ORDER BY enqueued_at, recalc_job_id
                    ) AS generation
                FROM recalc_job
                WHERE trigger_ref_type IS NOT NULL
                  AND TRIM(trigger_ref_type) <> ''
                  AND trigger_ref_id IS NOT NULL
                  AND TRIM(trigger_ref_id) <> ''
            ) AS ranked
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO recalc_invalidation_state (
                instrument_id,
                job_type,
                requested_generation,
                completed_generation,
                updated_at
            )
            SELECT
                instrument_id,
                job_type,
                MAX(generation),
                0,
                CURRENT_TIMESTAMP
            FROM recalc_source_event_inbox
            WHERE disposition = 'recalc'
            GROUP BY instrument_id, job_type
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE recalc_job
            SET claimed_generation = (
                SELECT inbox.generation
                FROM recalc_source_event_inbox AS inbox
                WHERE inbox.source_event_inbox_id = recalc_job.recalc_job_id
            )
            WHERE job_status IN ('running', 'completed', 'failed')
              AND recalc_job_id IN (
                  SELECT source_event_inbox_id
                  FROM recalc_source_event_inbox
                  WHERE disposition = 'recalc'
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE recalc_invalidation_state
            SET completed_generation = COALESCE(
                (
                    SELECT MAX(inbox.generation)
                    FROM recalc_source_event_inbox AS inbox
                    JOIN recalc_job AS job
                      ON job.recalc_job_id = inbox.source_event_inbox_id
                    WHERE inbox.instrument_id = recalc_invalidation_state.instrument_id
                      AND inbox.job_type = recalc_invalidation_state.job_type
                      AND job.job_status = 'completed'
                ),
                0
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE recalc_source_event_inbox
            SET consumed_at = COALESCE(consumed_at, received_at)
            WHERE disposition = 'recalc'
              AND generation <= COALESCE(
                  (
                      SELECT state.completed_generation
                      FROM recalc_invalidation_state AS state
                      WHERE state.instrument_id = recalc_source_event_inbox.instrument_id
                        AND state.job_type = recalc_source_event_inbox.job_type
                  ),
                  0
              )
            """
        )
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
