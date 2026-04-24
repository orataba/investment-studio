"""Use open-job dedupe index for recalc jobs.

Revision ID: 20260424_0008
Revises: 20260423_0007
Create Date: 2026-04-24 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260424_0008"
down_revision = "20260423_0007"
branch_labels = None
depends_on = None


OPEN_JOB_PREDICATE = "job_status IN ('queued', 'running')"


def upgrade() -> None:
    with op.batch_alter_table("recalc_job") as batch_op:
        batch_op.drop_constraint("uq_recalc_job_dedupe_key", type_="unique")
    op.create_index(
        "uq_recalc_job_open_dedupe_key",
        "recalc_job",
        ["dedupe_key"],
        unique=True,
        sqlite_where=sa.text(OPEN_JOB_PREDICATE),
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_recalc_job_open_dedupe_key", table_name="recalc_job")
    with op.batch_alter_table("recalc_job") as batch_op:
        batch_op.create_unique_constraint("uq_recalc_job_dedupe_key", ["dedupe_key"])
