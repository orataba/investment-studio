"""Add a compact chart projection for Watchlist list reads.

Existing charts are derived by refresh_release_watchlists before serving traffic.
The complete chart and its source clocks remain unchanged.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0059"
down_revision = "20260914_0058"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("instrument_chart_read_model", sa.Column("screener_payload_json", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    with op.batch_alter_table("instrument_chart_read_model") as batch:
        batch.drop_column("screener_payload_json")
