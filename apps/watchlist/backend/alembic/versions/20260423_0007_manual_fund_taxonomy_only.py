"""remove auto-generated fund taxonomy assignments and keep taxonomy manual

Revision ID: 20260423_0007
Revises: 20260423_0006
Create Date: 2026-04-23 00:07:00
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260423_0007"
down_revision = "20260423_0006"
branch_labels = None
depends_on = None


FUND_TAXONOMY_CODE = "fund_taxonomy"
AUTO_SOURCE_PREFIX = "migration/"
FUND_TAXONOMY_DERIVED_KEYS = {
    "fund_regime",
    "fund_taxonomy_leaf",
    "fund_taxonomy_path",
    *{f"fund_taxonomy_level_{level}" for level in range(1, 7)},
}


def _serialize_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, ensure_ascii=False))
    return value


def _empty_taxonomy_context() -> dict[str, object]:
    return {
        "taxonomy_code": FUND_TAXONOMY_CODE,
        "assigned_node_id": None,
        "assigned_label": None,
        "path_labels": [],
        "path_node_ids": [],
        "depth": 0,
        "derived_values": {},
    }


def _strip_taxonomy_attributes(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if str(key) not in FUND_TAXONOMY_DERIVED_KEYS
    }


def upgrade() -> None:
    bind = op.get_bind()
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("asset_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("source_record_id", sa.String()),
    )
    watchlist_row_table = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("asset_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    summary_table = sa.table(
        "asset_summary_read_model",
        sa.column("asset_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    affected_asset_ids = [
        str(row["asset_id"])
        for row in bind.execute(
            sa.select(assignment_table.c.asset_id).where(
                assignment_table.c.taxonomy_code == FUND_TAXONOMY_CODE,
                assignment_table.c.source_record_id.like(f"{AUTO_SOURCE_PREFIX}%"),
            )
        ).mappings()
    ]
    if not affected_asset_ids:
        return

    bind.execute(
        sa.delete(assignment_table).where(
            assignment_table.c.taxonomy_code == FUND_TAXONOMY_CODE,
            assignment_table.c.asset_id.in_(affected_asset_ids),
            assignment_table.c.source_record_id.like(f"{AUTO_SOURCE_PREFIX}%"),
        )
    )

    empty_taxonomy_context = _empty_taxonomy_context()

    for row in bind.execute(
        sa.select(watchlist_row_table).where(
            watchlist_row_table.c.asset_id.in_(affected_asset_ids)
        )
    ).mappings():
        bind.execute(
            sa.update(watchlist_row_table)
            .where(
                watchlist_row_table.c.watchlist_id == row["watchlist_id"],
                watchlist_row_table.c.asset_id == row["asset_id"],
            )
            .values(
                attributes_json=_serialize_json(
                    _strip_taxonomy_attributes(row["attributes_json"])
                )
            )
        )

    for row in bind.execute(
        sa.select(summary_table).where(summary_table.c.asset_id.in_(affected_asset_ids))
    ).mappings():
        payload = row["payload_json"] if isinstance(row["payload_json"], dict) else {}
        next_payload = dict(payload)
        next_payload.pop("classification", None)
        next_payload["taxonomy"] = empty_taxonomy_context
        if isinstance(next_payload.get("instrument_attributes"), dict):
            next_payload["instrument_attributes"] = _strip_taxonomy_attributes(
                next_payload["instrument_attributes"]
            )
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.asset_id == row["asset_id"])
            .values(payload_json=_serialize_json(next_payload))
        )


def downgrade() -> None:
    pass
