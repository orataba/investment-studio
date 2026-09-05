"""Rebind captured function paths after the asset-data schema rename."""

from alembic import op

revision = "20260904_0032"
down_revision = "20260904_0031"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "enforce_fund_nav_current_projection_contract",
    "enforce_fund_nav_event_instrument_contract",
    "enforce_fund_nav_factor_event_contract",
    "enforce_fund_nav_market_data_factor_contract",
    "enforce_fund_nav_projection_run_contract",
    "enforce_fund_nav_projection_run_event_contract",
    "enforce_fund_nav_projection_run_reinvestment_evidence_contract",
    "enforce_fund_nav_reinvestment_evidence_contract",
    "enforce_instrument_fund_nav_contract",
    "enforce_instrument_market_data_price_contract",
    "enforce_instrument_type_price_contract",
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # ALTER SCHEMA preserves function identity, but not names in proconfig.
        for name in FUNCTIONS:
            op.execute(
                f"ALTER FUNCTION instrument_data.{name}() "
                "SET search_path TO instrument_data, public"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name in FUNCTIONS:
            op.execute(
                f"ALTER FUNCTION instrument_data.{name}() SET search_path FROM CURRENT"
            )
