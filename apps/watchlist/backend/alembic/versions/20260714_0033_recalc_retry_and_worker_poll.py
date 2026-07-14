"""Add bounded recalc retry and worker poll readiness state.

Revision ID: 20260714_0033
Revises: 20260714_0032
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0033"
down_revision = "20260714_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recalc_job",
        sa.Column("attempt_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "recalc_job",
        sa.Column("max_attempts", sa.Integer(), nullable=True),
    )
    op.add_column(
        "recalc_job",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE recalc_job
            SET attempt_count = CASE
                    WHEN job_status = 'failed' THEN 3
                    WHEN job_status IN ('running', 'completed') THEN 1
                    ELSE 0
                END,
                max_attempts = 3,
                available_at = COALESCE(enqueued_at, CURRENT_TIMESTAMP)
            """
        )
    )
    with op.batch_alter_table("recalc_job") as batch_op:
        batch_op.alter_column(
            "attempt_count",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        )
        batch_op.alter_column(
            "max_attempts",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        )
        batch_op.alter_column(
            "available_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )
        batch_op.create_check_constraint(
            op.f("ck_recalc_job_attempt_budget"),
            "attempt_count >= 0 AND max_attempts > 0 "
            "AND attempt_count <= max_attempts",
        )

    op.drop_index("idx_recalc_job_status_priority", table_name="recalc_job")
    op.create_index(
        "idx_recalc_job_claim",
        "recalc_job",
        ["job_status", "available_at", "priority", "enqueued_at"],
        unique=False,
    )

    op.add_column(
        "recalc_worker_registration",
        sa.Column(
            "last_successful_poll_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "recalc_worker_registration",
        sa.Column("last_poll_error", sa.String(length=4000), nullable=True),
    )
    op.add_column(
        "recalc_worker_registration",
        sa.Column("worker_state", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "recalc_worker_registration",
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE recalc_worker_registration
            SET worker_state = 'running'
            WHERE worker_state IS NULL
            """
        )
    )
    with op.batch_alter_table("recalc_worker_registration") as batch_op:
        batch_op.alter_column(
            "worker_state",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default=sa.text("'running'"),
        )
        batch_op.create_check_constraint(
            op.f("ck_recalc_worker_registration_state"),
            "worker_state IN ('running', 'stopped')",
        )
        batch_op.create_check_constraint(
            op.f("ck_recalc_worker_registration_lifecycle"),
            "(worker_state = 'running' AND stopped_at IS NULL) OR "
            "(worker_state = 'stopped' AND stopped_at IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("recalc_worker_registration") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_recalc_worker_registration_lifecycle"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_recalc_worker_registration_state"),
            type_="check",
        )
        batch_op.drop_column("stopped_at")
        batch_op.drop_column("worker_state")
        batch_op.drop_column("last_poll_error")
        batch_op.drop_column("last_successful_poll_at")

    op.drop_index("idx_recalc_job_claim", table_name="recalc_job")
    op.create_index(
        "idx_recalc_job_status_priority",
        "recalc_job",
        ["job_status", "priority", "enqueued_at"],
        unique=False,
    )
    with op.batch_alter_table("recalc_job") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_recalc_job_attempt_budget"),
            type_="check",
        )
        batch_op.drop_column("available_at")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt_count")
