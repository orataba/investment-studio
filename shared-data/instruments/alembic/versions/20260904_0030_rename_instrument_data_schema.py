"""Rename the shared asset-data schema without moving or rewriting facts."""

from alembic import op

revision = "20260904_0030"
down_revision = "20260902_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER SCHEMA instrument_registry RENAME TO instrument_data")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER SCHEMA instrument_data RENAME TO instrument_registry")
