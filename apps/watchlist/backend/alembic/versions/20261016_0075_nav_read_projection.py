"""Publish NAV detail with the chart and metric generation.

Existing rows are rebuilt by the normal materialization release/worker path.
The migration does not infer analytical NAV from an older display chart.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0075"
down_revision = "20261014_0074"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("instrument_chart_read_model", sa.Column("nav_payload_json", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    with op.batch_alter_table("instrument_chart_read_model") as batch:
        batch.drop_column("nav_payload_json")
