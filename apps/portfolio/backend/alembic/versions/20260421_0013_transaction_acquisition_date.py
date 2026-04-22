"""transaction acquisition date

Revision ID: 20260421_0013
Revises: 20260421_0012
Create Date: 2026-04-21 01:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260421_0013"
down_revision = "20260421_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transaction_record", sa.Column("acquisition_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction_record", "acquisition_date")
