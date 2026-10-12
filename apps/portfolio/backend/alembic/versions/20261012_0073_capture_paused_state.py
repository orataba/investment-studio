"""Keep an operator-paused screenshot attempt distinct from a failed attempt."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_0073"
down_revision = "20261008_0072"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_transaction_capture_batch_analysis_run_status"
OLD_STATES = "'idle', 'queued', 'running', 'succeeded', 'failed'"


def upgrade():
    # 0056 supplied a full name to the naming convention, which prepended the
    # table prefix again. Drop that existing generated name; create the canonical
    # model name explicitly for the new constraint.
    with op.batch_alter_table("transaction_capture_batch") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(op.f(CONSTRAINT), f"analysis_run_status IN ({OLD_STATES}, 'paused')")


def downgrade():
    # A rollback must not relabel an intentional pause as success/failure or
    # discard its originals. The older schema cannot represent that fact.
    if op.get_bind().execute(sa.text("SELECT 1 FROM transaction_capture_batch WHERE analysis_run_status='paused' LIMIT 1")).first():
        raise RuntimeError("Paused screenshot attempts exist; downgrade cannot preserve their status.")
    with op.batch_alter_table("transaction_capture_batch") as batch:
        batch.drop_constraint(op.f(CONSTRAINT), type_="check")
        batch.create_check_constraint(CONSTRAINT, f"analysis_run_status IN ({OLD_STATES})")
