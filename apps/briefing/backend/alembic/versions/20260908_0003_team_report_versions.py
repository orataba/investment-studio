"""Keep report revision numbers within the owning team's publication period."""
from alembic import op

revision = "20260908_0003"
down_revision = "20260908_0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("report") as batch:
        batch.drop_constraint("uq_briefing_report_version", type_="unique")
        batch.create_unique_constraint("uq_briefing_report_version", ["team_id", "report_type", "report_date", "version"])


def downgrade():
    # Restoring the old contract is permitted only if current teams' revision
    # numbers do not collide. The database rejects a lossy downgrade.
    with op.batch_alter_table("report") as batch:
        batch.drop_constraint("uq_briefing_report_version", type_="unique")
        batch.create_unique_constraint("uq_briefing_report_version", ["report_type", "report_date", "version"])
