"""Serialize email acquisition with a crash-recoverable mailbox lease.

Revision ID: 20260716_0002
Revises: 20260715_0001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0002"
down_revision: str | None = "20260715_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_mailbox_ingestion_lease",
        sa.Column("mailbox_key", sa.String(length=64), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_email_mailbox_ingestion_lease_lease_state_contract"),
        ),
        sa.PrimaryKeyConstraint(
            "mailbox_key",
            name="pk_email_mailbox_ingestion_lease",
        ),
    )


def downgrade() -> None:
    op.drop_table("email_mailbox_ingestion_lease")
