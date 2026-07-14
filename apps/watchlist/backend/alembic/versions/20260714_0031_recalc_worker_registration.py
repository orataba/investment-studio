"""Add persistent Watchlist recalc worker registration and heartbeat.

Revision ID: 20260714_0031
Revises: 20260713_0030
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0031"
down_revision = "20260713_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recalc_worker_registration",
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("instance_id", sa.String(length=32), nullable=False),
        sa.Column("worker_version", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(
            "worker_id",
            name=op.f("pk_recalc_worker_registration"),
        ),
        sa.UniqueConstraint(
            "instance_id",
            name=op.f("uq_recalc_worker_registration_instance_id"),
        ),
    )
    op.create_index(
        "idx_recalc_worker_registration_last_heartbeat",
        "recalc_worker_registration",
        ["last_heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_recalc_worker_registration_last_heartbeat",
        table_name="recalc_worker_registration",
    )
    op.drop_table("recalc_worker_registration")
