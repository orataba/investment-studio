"""Record future list creators without inventing historical ownership.

Revision ID: 20260914_0058
Revises: 20260908_0057
"""
from alembic import op
import sqlalchemy as sa

revision = "20260914_0058"
down_revision = "20260908_0057"
branch_labels = None
depends_on = None


def upgrade():
    # Existing owner_id is the team after 0056, not evidence of a creator.
    op.add_column("watchlist", sa.Column("created_by_user_id", sa.Text(), nullable=True))
    op.add_column("watchlist", sa.Column("created_by_display_name", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("watchlist") as batch:
        batch.drop_column("created_by_display_name")
        batch.drop_column("created_by_user_id")
