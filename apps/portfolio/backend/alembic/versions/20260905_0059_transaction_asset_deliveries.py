"""Store physical deliveries, explicit lots, cash purpose and contract amendments."""
from alembic import op
import sqlalchemy as sa

revision = "20260905_0059"
down_revision = "20260902_0058"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("account_record", sa.Column("cash_purpose", sa.String(), nullable=True))
    op.add_column("account_record", sa.Column("collateral_reference", sa.String(), nullable=True))
    with op.batch_alter_table("transaction_record") as batch:
        batch.drop_constraint("position_effective_date", type_="check")
        batch.create_check_constraint("position_effective_date", "position_effective_date IS NULL OR (transaction_type IN ('buy','sell','dividend_reinvestment','maturity_redemption','short_sell','buy_to_cover') AND position_effective_date >= trade_date)")
    op.add_column("transaction_record", sa.Column("asset_deliveries_json", sa.JSON(), nullable=True))
    op.add_column("transaction_record", sa.Column("lot_selections_json", sa.JSON(), nullable=True))
    op.add_column("derivative_contract_record", sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("derivative_contract_record", sa.Column("amendments_json", sa.JSON(), nullable=True))


def downgrade():
    connection = op.get_bind()
    used = connection.execute(sa.text("SELECT 1 FROM transaction_record WHERE transaction_type IN ('short_sell','buy_to_cover','short_opening_balance') OR CAST(asset_deliveries_json AS TEXT) NOT IN ('[]','null') OR CAST(lot_selections_json AS TEXT) NOT IN ('[]','null') LIMIT 1")).first()
    amended = connection.execute(sa.text("SELECT 1 FROM derivative_contract_record WHERE row_version > 1 LIMIT 1")).first()
    cash_metadata = connection.execute(sa.text("SELECT 1 FROM account_record WHERE cash_purpose IS NOT NULL OR collateral_reference IS NOT NULL LIMIT 1")).first()
    if used or amended or cash_metadata:
        raise RuntimeError("Cannot downgrade: recorded deliveries, short positions, selected lots, cash purposes or contract amendments would be lost. Export and reconcile these facts before a manual migration.")
    op.drop_column("account_record", "collateral_reference")
    op.drop_column("account_record", "cash_purpose")
    with op.batch_alter_table("transaction_record") as batch:
        batch.drop_constraint("position_effective_date", type_="check")
        batch.create_check_constraint("position_effective_date", "position_effective_date IS NULL OR (transaction_type IN ('buy','sell','dividend_reinvestment','maturity_redemption') AND position_effective_date >= trade_date)")
    op.drop_column("derivative_contract_record", "amendments_json")
    op.drop_column("derivative_contract_record", "row_version")
    op.drop_column("transaction_record", "asset_deliveries_json")
    op.drop_column("transaction_record", "lot_selections_json")
