"""Reconcile Watchlist identifiers after historical revisions were renamed in place.

Revision ID: 20260809_0034
Revises: 20260809_0033
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0034"
down_revision = "20260809_0033"
branch_labels = None
depends_on = None


CONSTRAINT_RENAMES = (
    ("exposure_analytics_snapshot", "fk_exposure_analytics_snapshot_asset_id_asset_detail", "fk_exposure_analytics_snapshot_instrument_id_instrument_detail"),
    ("holding_snapshot", "fk_holding_snapshot_asset_id_asset_detail", "fk_holding_snapshot_instrument_id_instrument_detail"),
    ("instrument_attribute_value", "fk_instrument_attribute_value_asset_id_asset_detail", "fk_instrument_attribute_value_instrument_id_instrument_detail"),
    ("instrument_chart_read_model", "pk_asset_chart_read_model", "pk_instrument_chart_read_model"),
    ("instrument_chart_read_model", "fk_asset_chart_read_model_asset_id_asset_detail", "fk_instrument_chart_read_model_instrument_id_instrument_detail"),
    ("instrument_detail", "pk_asset_detail", "pk_instrument_detail"),
    ("instrument_detail", "fk_asset_detail_asset_id_instrument", "fk_instrument_detail_instrument_id_instrument"),
    ("instrument_exposure_holdings_read_model", "pk_asset_exposure_holdings_read_model", "pk_instrument_exposure_holdings_read_model"),
    ("instrument_exposure_holdings_read_model", "fk_asset_exposure_holdings_read_model_asset_id_asset_detail", "fk_instrument_exposure_holdings_read_model_instrument_i_4381"),
    ("instrument_exposure_read_model", "pk_asset_exposure_read_model", "pk_instrument_exposure_read_model"),
    ("instrument_exposure_read_model", "fk_asset_exposure_read_model_asset_id_asset_detail", "fk_instrument_exposure_read_model_instrument_id_instrum_0346"),
    ("instrument_manual_profile", "pk_asset_manual_profile", "pk_instrument_manual_profile"),
    ("instrument_manual_profile", "fk_asset_manual_profile_asset_id_asset_detail", "fk_instrument_manual_profile_instrument_id_instrument_detail"),
    ("instrument_performance_read_model", "pk_asset_performance_read_model", "pk_instrument_performance_read_model"),
    ("instrument_performance_read_model", "fk_asset_performance_read_model_asset_id_asset_detail", "fk_instrument_performance_read_model_instrument_id_inst_9937"),
    ("instrument_risk_read_model", "pk_asset_risk_read_model", "pk_instrument_risk_read_model"),
    ("instrument_risk_read_model", "fk_asset_risk_read_model_asset_id_asset_detail", "fk_instrument_risk_read_model_instrument_id_instrument_detail"),
    ("instrument_summary_read_model", "pk_asset_summary_read_model", "pk_instrument_summary_read_model"),
    ("instrument_summary_read_model", "fk_asset_summary_read_model_asset_id_asset_detail", "fk_instrument_summary_read_model_instrument_id_instrume_1b8e"),
    ("instrument_taxonomy_assignment", "fk_instrument_taxonomy_assignment_asset_id_asset_detail", "fk_instrument_taxonomy_assignment_instrument_id_instrum_983f"),
    ("nav_fact", "fk_nav_fact_asset_id_asset_detail", "fk_nav_fact_instrument_id_instrument_detail"),
    ("nav_fact", "uq_nav_fact_asset_date_type_currency", "uq_nav_fact_instrument_date_type_currency"),
    ("performance_snapshot", "fk_performance_snapshot_asset_id_asset_detail", "fk_performance_snapshot_instrument_id_instrument_detail"),
    ("recalc_job", "fk_recalc_job_asset_id_asset_detail", "fk_recalc_job_instrument_id_instrument_detail"),
    ("risk_snapshot", "fk_risk_snapshot_asset_id_asset_detail", "fk_risk_snapshot_instrument_id_instrument_detail"),
    ("watchlist_item", "fk_watchlist_item_asset_id_instrument", "fk_watchlist_item_instrument_id_instrument"),
)

INDEX_RENAMES = (
    ("idx_instrument_attribute_value_asset_attribute", "idx_instrument_attribute_value_instrument_attribute"),
    ("idx_nav_fact_asset_date", "idx_nav_fact_instrument_date"),
)


def _constraint_exists(bind, table_name: str, constraint_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                "WHERE ns.nspname = current_schema() AND rel.relname = :table_name "
                "AND con.conname = :constraint_name"
            ),
            {"table_name": table_name, "constraint_name": constraint_name},
        ).first()
    )


def _index_exists(bind, index_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class idx "
                "JOIN pg_namespace ns ON ns.oid = idx.relnamespace "
                "WHERE ns.nspname = current_schema() AND idx.relkind = 'i' "
                "AND idx.relname = :index_name"
            ),
            {"index_name": index_name},
        ).first()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    schema = bind.dialect.identifier_preparer.quote_schema(
        str(bind.scalar(sa.text("SELECT current_schema()")))
    )
    for table_name, legacy_name, canonical_name in CONSTRAINT_RENAMES:
        legacy_exists = _constraint_exists(bind, table_name, legacy_name)
        canonical_exists = _constraint_exists(bind, table_name, canonical_name)
        if legacy_exists and not canonical_exists:
            op.execute(
                f'ALTER TABLE {schema}."{table_name}" '
                f'RENAME CONSTRAINT "{legacy_name}" TO "{canonical_name}"'
            )
        elif legacy_exists == canonical_exists:
            raise RuntimeError(
                f"Expected exactly one of {legacy_name!r} or {canonical_name!r} "
                f"on watchlist.{table_name}."
            )
    for legacy_name, canonical_name in INDEX_RENAMES:
        legacy_exists = _index_exists(bind, legacy_name)
        canonical_exists = _index_exists(bind, canonical_name)
        if legacy_exists and not canonical_exists:
            op.execute(
                f'ALTER INDEX {schema}."{legacy_name}" RENAME TO "{canonical_name}"'
            )
        elif legacy_exists == canonical_exists:
            raise RuntimeError(
                f"Expected exactly one of {legacy_name!r} or {canonical_name!r}."
            )


def downgrade() -> None:
    raise RuntimeError(
        "Identifier reconciliation is the canonical schema state and is not downgradable."
    )
