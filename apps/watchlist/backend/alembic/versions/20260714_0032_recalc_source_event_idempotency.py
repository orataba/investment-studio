"""Add a lookup index for Watchlist recalc source references.

Revision ID: 20260714_0032
Revises: 20260714_0031

``recalc_job`` is an execution audit table.  Repeated executions may carry the
same source reference, so the index deliberately is not unique.  Durable
source-event idempotency is introduced on ``recalc_source_event_inbox`` in
revision 0034.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0032"
down_revision = "20260714_0031"
branch_labels = None
depends_on = None


SOURCE_REFERENCE_INDEX = "idx_recalc_job_source_reference"
SOURCE_REFERENCE_PREDICATE = (
    "trigger_ref_type IS NOT NULL AND TRIM(trigger_ref_type) <> '' "
    "AND trigger_ref_id IS NOT NULL AND TRIM(trigger_ref_id) <> ''"
)


def upgrade() -> None:
    op.create_index(
        SOURCE_REFERENCE_INDEX,
        "recalc_job",
        ["trigger_ref_type", "trigger_ref_id", "instrument_id", "job_type"],
        unique=False,
        sqlite_where=sa.text(SOURCE_REFERENCE_PREDICATE),
        postgresql_where=sa.text(SOURCE_REFERENCE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(SOURCE_REFERENCE_INDEX, table_name="recalc_job")
