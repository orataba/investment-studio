"""Make Watchlist recalc source-event consumption idempotent.

Revision ID: 20260714_0032
Revises: 20260714_0031
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0032"
down_revision = "20260714_0031"
branch_labels = None
depends_on = None


SOURCE_EVENT_INDEX = "uq_recalc_job_source_event_identity"
SOURCE_EVENT_PREDICATE = (
    "trigger_ref_type IS NOT NULL AND TRIM(trigger_ref_type) <> '' "
    "AND trigger_ref_id IS NOT NULL AND TRIM(trigger_ref_id) <> ''"
)


def upgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT
                trigger_ref_type,
                trigger_ref_id,
                instrument_id,
                job_type,
                COUNT(*) AS duplicate_count
            FROM recalc_job
            WHERE trigger_ref_type IS NOT NULL
              AND TRIM(trigger_ref_type) <> ''
              AND trigger_ref_id IS NOT NULL
              AND TRIM(trigger_ref_id) <> ''
            GROUP BY
                trigger_ref_type,
                trigger_ref_id,
                instrument_id,
                job_type
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot enforce recalc source-event idempotency: duplicate source-event "
            "identity exists for "
            f"trigger_ref_type={duplicate['trigger_ref_type']!r}, "
            f"trigger_ref_id={duplicate['trigger_ref_id']!r}, "
            f"instrument_id={duplicate['instrument_id']!r}, "
            f"job_type={duplicate['job_type']!r}, "
            f"count={duplicate['duplicate_count']}."
        )

    op.create_index(
        SOURCE_EVENT_INDEX,
        "recalc_job",
        ["trigger_ref_type", "trigger_ref_id", "instrument_id", "job_type"],
        unique=True,
        sqlite_where=sa.text(SOURCE_EVENT_PREDICATE),
        postgresql_where=sa.text(SOURCE_EVENT_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(SOURCE_EVENT_INDEX, table_name="recalc_job")
