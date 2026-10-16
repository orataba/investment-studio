"""Publish risk-workspace price inputs with their metric generation.

Release materialization rebuilds these from the retained research-return path;
the migration never infers readings or changes risk cases/history.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0076"
down_revision = "20261016_0075"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("instrument_risk_read_model", sa.Column("workspace_price_context_json", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    with op.batch_alter_table("instrument_risk_read_model") as batch:
        batch.drop_column("workspace_price_context_json")
