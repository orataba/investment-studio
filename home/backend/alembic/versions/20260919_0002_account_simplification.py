"""Let invitees choose usernames and retire the unused two-factor workflow."""
from alembic import op
import sqlalchemy as sa

revision = "20260919_0002"
down_revision = "20260908_0001"
branch_labels = None
depends_on = None


def upgrade():
    schema = "identity" if op.get_bind().dialect.name == "postgresql" else None
    with op.batch_alter_table("users", schema=schema) as batch:
        batch.alter_column("username", existing_type=sa.String(128), nullable=True)
        batch.drop_column("totp_secret")
        batch.drop_column("totp_pending_secret")
        batch.drop_column("totp_last_step")


def downgrade():
    raise RuntimeError("Two-factor secrets cannot be restored. Restore the pre-migration identity backup to roll back.")
