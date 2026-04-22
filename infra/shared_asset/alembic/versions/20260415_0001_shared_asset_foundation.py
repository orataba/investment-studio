"""shared asset foundation

Revision ID: 20260415_0001
Revises:
Create Date: 2026-04-15 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260415_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_metadata",
        sa.Column("registry_key", sa.String(), nullable=False),
        sa.Column("registry_name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("registry_key", name=op.f("pk_registry_metadata")),
    )
    op.create_table(
        "instrument",
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("asset_name", sa.String(), nullable=False),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("quote_selection_policy_json", sa.JSON(), nullable=False),
        sa.Column("source_settings_json", sa.JSON(), nullable=False),
        sa.Column("refresh_status_json", sa.JSON(), nullable=False),
        sa.Column("lifecycle_state_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", name=op.f("pk_instrument")),
    )
    op.create_table(
        "instrument_identifier",
        sa.Column("instrument_identifier_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("identifier_type", sa.String(), nullable=False),
        sa.Column("identifier_value", sa.String(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["instrument.asset_id"],
            name=op.f("fk_instrument_identifier_asset_id_instrument"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instrument_identifier_id", name=op.f("pk_instrument_identifier")),
        sa.UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_instrument_identifier_type_value",
        ),
    )
    op.create_table(
        "instrument_market_data",
        sa.Column("instrument_market_data_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("metric_family", sa.String(), nullable=False),
        sa.Column("quote_basis", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["instrument.asset_id"],
            name=op.f("fk_instrument_market_data_asset_id_instrument"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instrument_market_data_id", name=op.f("pk_instrument_market_data")),
        sa.UniqueConstraint(
            "asset_id",
            "metric_family",
            "quote_basis",
            "as_of_date",
            "currency",
            name="uq_instrument_market_data_asset_metric_basis_date_currency",
        ),
    )


def downgrade() -> None:
    op.drop_table("instrument_market_data")
    op.drop_table("instrument_identifier")
    op.drop_table("instrument")
    op.drop_table("registry_metadata")
