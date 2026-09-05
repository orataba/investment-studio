"""Remove captured legacy schema names from the ingestion trigger."""

from alembic import op

revision = "20260904_0009"
down_revision = "20260904_0008"
branch_labels = None
depends_on = None

FUNCTION = "data_ingestion.enforce_fund_nav_action_candidate_terminal_status()"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"ALTER FUNCTION {FUNCTION} "
            "SET search_path TO data_ingestion, instrument_data, public"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"ALTER FUNCTION {FUNCTION} SET search_path FROM CURRENT")
