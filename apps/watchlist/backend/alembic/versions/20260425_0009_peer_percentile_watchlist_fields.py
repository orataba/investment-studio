"""add taxonomy peer percentile watchlist fields

Revision ID: 20260425_0009
Revises: 20260424_0008
Create Date: 2026-04-25 00:00:00
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260425_0009"
down_revision = "20260424_0008"
branch_labels = None
depends_on = None


PEER_FIELD_KEYS = {
    "attr.peer_group",
    "attr.peer_sample_count",
    "attr.peer_overall_percentile",
    "attr.peer_return_percentile",
    "attr.peer_risk_percentile",
    "attr.peer_risk_adjusted_percentile",
    "attr.peer_return_1w_percentile",
    "attr.peer_return_1m_percentile",
    "attr.peer_return_ytd_percentile",
    "attr.peer_return_1y_percentile",
    "attr.peer_return_3y_percentile",
    "attr.peer_return_5y_percentile",
    "attr.peer_annualized_return_percentile",
    "attr.peer_volatility_percentile",
    "attr.peer_max_drawdown_percentile",
    "attr.peer_sharpe_percentile",
    "attr.peer_calmar_percentile",
}


def _serialize_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, ensure_ascii=False))
    return value


def _upsert_field_registry_rows(bind, field_registry_table, rows: list[dict[str, object]]) -> None:
    existing_keys = {
        str(item["field_key"])
        for item in bind.execute(sa.select(field_registry_table.c.field_key)).mappings()
    }
    for row in rows:
        payload = {key: _serialize_json(value) for key, value in row.items()}
        if str(payload["field_key"]) in existing_keys:
            bind.execute(
                sa.update(field_registry_table)
                .where(field_registry_table.c.field_key == payload["field_key"])
                .values(**payload)
            )
        else:
            bind.execute(sa.insert(field_registry_table).values(**payload))


def upgrade() -> None:
    from watchlist_app.reference_data.watchlist_fields import FIELD_REGISTRY

    bind = op.get_bind()
    field_registry_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("category_code", sa.String()),
        sa.column("data_type", sa.String()),
        sa.column("formatter_code", sa.String()),
        sa.column("sort_mode", sa.String()),
        sa.column("filter_mode", sa.String()),
        sa.column("group_mode", sa.String()),
        sa.column("asset_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )
    _upsert_field_registry_rows(
        bind,
        field_registry_table,
        [row for row in FIELD_REGISTRY if str(row["field_key"]) in PEER_FIELD_KEYS],
    )


def downgrade() -> None:
    bind = op.get_bind()
    field_registry_table = sa.table(
        "field_registry",
        sa.column("field_key", sa.String()),
    )
    bind.execute(
        sa.delete(field_registry_table).where(
            field_registry_table.c.field_key.in_(sorted(PEER_FIELD_KEYS))
        )
    )
