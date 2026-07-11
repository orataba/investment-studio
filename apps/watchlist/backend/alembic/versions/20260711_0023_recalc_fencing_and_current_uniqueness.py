"""Fence recalc workers and enforce one current materialization.

Revision ID: 20260711_0023
Revises: 20260711_0022
"""

from alembic import op
import sqlalchemy as sa


revision = "20260711_0023"
down_revision = "20260711_0022"
branch_labels = None
depends_on = None


CURRENT_TABLES = (
    ("performance_snapshot", "snapshot_id", "uq_performance_snapshot_current_instrument"),
    ("risk_snapshot", "snapshot_id", "uq_risk_snapshot_current_instrument"),
    ("exposure_analytics_snapshot", "snapshot_id", "uq_exposure_snapshot_current_instrument"),
    ("instrument_score_snapshot", "snapshot_id", "uq_score_snapshot_current_instrument"),
    ("holding_snapshot", "holding_snapshot_id", "uq_holding_snapshot_current_instrument"),
)


def _retire_duplicate_current_rows(table_name: str, primary_key: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET is_current = :false_value,
                superseded_at = COALESCE(superseded_at, CURRENT_TIMESTAMP)
            WHERE {primary_key} IN (
                SELECT {primary_key}
                FROM (
                    SELECT {primary_key},
                           ROW_NUMBER() OVER (
                               PARTITION BY instrument_id
                               ORDER BY calculated_at DESC, {primary_key} DESC
                           ) AS row_number
                    FROM {table_name}
                    WHERE is_current = :true_value
                ) ranked
                WHERE row_number > 1
            )
            """
        ).bindparams(false_value=False, true_value=True)
    )


def upgrade() -> None:
    op.add_column("recalc_job", sa.Column("lease_token", sa.String(), nullable=True))

    # Deployment replaces all workers, so pre-existing running jobs must be reclaimed
    # with a new lease before any worker may publish their results.
    op.execute(
        sa.text(
            """
            UPDATE recalc_job
            SET job_status = 'queued',
                started_at = NULL,
                heartbeat_at = NULL,
                finished_at = NULL,
                lease_token = NULL,
                error_message = 'Requeued during lease-fencing migration.'
            WHERE job_status = 'running'
            """
        )
    )

    # Historical trigger-specific keys may have produced duplicate queued work.
    op.execute(
        sa.text(
            """
            UPDATE recalc_job
            SET job_status = 'failed',
                finished_at = CURRENT_TIMESTAMP,
                error_message = 'Superseded duplicate open job during dedupe migration.'
            WHERE recalc_job_id IN (
                SELECT recalc_job_id
                FROM (
                    SELECT recalc_job_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY instrument_id, job_type
                               ORDER BY priority DESC, enqueued_at ASC, recalc_job_id ASC
                           ) AS row_number
                    FROM recalc_job
                    WHERE job_status IN ('queued', 'running')
                ) ranked
                WHERE row_number > 1
            )
            """
        )
    )

    op.create_index(
        "uq_recalc_job_running_instrument",
        "recalc_job",
        ["instrument_id"],
        unique=True,
        postgresql_where=sa.text("job_status = 'running'"),
        sqlite_where=sa.text("job_status = 'running'"),
    )

    for table_name, primary_key, index_name in CURRENT_TABLES:
        _retire_duplicate_current_rows(table_name, primary_key)
        op.create_index(
            index_name,
            table_name,
            ["instrument_id"],
            unique=True,
            postgresql_where=sa.text("is_current IS TRUE"),
            sqlite_where=sa.text("is_current = 1"),
        )


def downgrade() -> None:
    for table_name, _primary_key, index_name in reversed(CURRENT_TABLES):
        op.drop_index(index_name, table_name=table_name)
    op.drop_index("uq_recalc_job_running_instrument", table_name="recalc_job")
    op.drop_column("recalc_job", "lease_token")
