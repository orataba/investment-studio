"""Persist workspace projections and rebuild the corrected input-generation baseline."""
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "20260920_0065"
down_revision = "20260920_0064"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolio_workspace_read_model",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("surface", sa.String(), primary_key=True),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("calculated_at", sa.String(), nullable=False),
        sa.Column("error_type", sa.String(), nullable=True),
    )
    # Earlier per-instrument clocks could hide a later configuration commit
    # behind another instrument's MAX watermark. The corrected allocator can
    # only order future writes. Rebuild disposable historical results once so
    # their new durable page projections start from today's actual facts.
    connection = op.get_bind()
    ids = connection.execute(sa.text("SELECT portfolio_id FROM portfolio_record")).scalars().all()
    for portfolio_id in ids:
        values = {"id": portfolio_id, "request": str(uuid4())}
        result = connection.execute(sa.text(
            "UPDATE portfolio_calculation_state SET daily_snapshot_status = 'stale', "
            "dirty_from = NULL, refresh_request_id = :request, error_message = NULL "
            "WHERE portfolio_id = :id"
        ), values)
        if result.rowcount == 0:
            connection.execute(sa.text(
                "INSERT INTO portfolio_calculation_state "
                "(portfolio_id, daily_snapshot_status, dirty_from, refresh_request_id) "
                "VALUES (:id, 'stale', NULL, :request)"
            ), values)


def downgrade():
    op.drop_table("portfolio_workspace_read_model")
