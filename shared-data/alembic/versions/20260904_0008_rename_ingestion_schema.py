"""Name the private data-ingestion schema by its responsibility.

Revision ID: 20260904_0008
Revises: 20260823_0007
"""

from alembic import op


revision = "20260904_0008"
down_revision = "20260823_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # PostgreSQL keeps table identity, rows, foreign keys and grants intact.
        op.execute("ALTER SCHEMA platform RENAME TO data_ingestion")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER SCHEMA data_ingestion RENAME TO platform")
