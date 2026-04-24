"""rebuild fund taxonomy structure to the new public/private strategy tree

Revision ID: 20260423_0006
Revises: 20260423_0005
Create Date: 2026-04-23 00:06:00
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260423_0006"
down_revision = "20260423_0005"
branch_labels = None
depends_on = None


FUND_TAXONOMY_CODE = "fund_taxonomy"


OLD_NODE_ID_TO_NEW_NODE_ID = {
    "fund-public": "fund-public",
    "fund-public-etf": "fund-public-equity-indexed",
    "fund-public-etf-broad": "fund-public-equity-indexed",
    "fund-public-etf-broad-300": "fund-public-equity-indexed",
    "fund-public-etf-broad-500": "fund-public-equity-indexed",
    "fund-public-etf-broad-1000": "fund-public-equity-indexed",
    "fund-public-etf-broad-2000": "fund-public-equity-indexed",
    "fund-public-etf-broad-a500": "fund-public-equity-indexed",
    "fund-public-etf-sector": "fund-public-equity-indexed",
    "fund-public-etf-bond": "fund-public-bond-indexed",
    "fund-public-etf-commodity": "fund-public-commodity-other",
    "fund-public-etf-crossborder": "fund-public-qdii-equity",
    "fund-public-index-enhancement": "fund-public-equity-indexed",
    "fund-public-index-enhancement-broad": "fund-public-equity-indexed",
    "fund-public-index-enhancement-300": "fund-public-equity-indexed",
    "fund-public-index-enhancement-500": "fund-public-equity-indexed",
    "fund-public-index-enhancement-1000": "fund-public-equity-indexed",
    "fund-public-index-enhancement-2000": "fund-public-equity-indexed",
    "fund-public-index-enhancement-a500": "fund-public-equity-indexed",
    "fund-public-index-enhancement-theme": "fund-public-equity-indexed",
    "fund-public-discretionary-long": "fund-public-equity-standard",
    "fund-public-discretionary-long-equity": "fund-public-equity-standard",
    "fund-public-discretionary-long-equity-general": "fund-public-equity-standard",
    "fund-public-discretionary-long-equity-theme": "fund-public-equity-standard",
    "fund-public-discretionary-long-equity-hk": "fund-public-equity-standard",
    "fund-public-discretionary-long-hybrid": "fund-public-hybrid-equity-biased",
    "fund-public-fixed-income": "fund-public-bond-pure",
    "fund-public-fixed-income-rates": "fund-public-bond-pure",
    "fund-public-fixed-income-credit": "fund-public-bond-ordinary",
    "fund-public-fixed-income-short-duration": "fund-public-bond-pure",
    "fund-public-convertible-bond": "fund-public-bond-convertible",
    "fund-public-convertible-bond-enhanced": "fund-public-bond-convertible",
    "fund-public-convertible-bond-balanced": "fund-public-bond-convertible",
    "fund-public-fixed-plus": "fund-public-hybrid-bond-biased",
    "fund-public-fixed-plus-secondary-bond": "fund-public-hybrid-bond-biased",
    "fund-public-fixed-plus-mixed": "fund-public-hybrid-bond-biased",
    "fund-public-fixed-plus-absolute-return": "fund-public-hybrid-strategy",
    "fund-public-multi-asset": "fund-public-hybrid-balanced",
    "fund-public-multi-asset-fof": "fund-public-fof-hybrid",
    "fund-public-multi-asset-pension-fof": "fund-public-fof-pension",
    "fund-public-multi-asset-balanced": "fund-public-hybrid-balanced",
    "fund-public-qdii": "fund-public-qdii",
    "fund-public-qdii-equity": "fund-public-qdii-equity",
    "fund-public-qdii-bond": "fund-public-qdii-bond",
    "fund-public-qdii-commodity": "fund-public-qdii-commodity",
    "fund-public-reits": "fund-public-reits",
    "fund-public-money-market": "fund-public-other",
    "fund-private": "fund-private",
    "fund-private-index-enhancement": "fund-private-equity-quant-long",
    "fund-private-index-enhancement-300": "fund-private-equity-quant-long-300",
    "fund-private-index-enhancement-500": "fund-private-equity-quant-long-500",
    "fund-private-index-enhancement-1000": "fund-private-equity-quant-long-1000",
    "fund-private-index-enhancement-2000": "fund-private-equity-quant-long-2000",
    "fund-private-index-enhancement-a500": "fund-private-equity-quant-long-other-enhanced",
    "fund-private-discretionary-long": "fund-private-equity-discretionary-long",
    "fund-private-discretionary-long-general": "fund-private-equity-discretionary-stock-picking",
    "fund-private-discretionary-long-growth": "fund-private-equity-discretionary-stock-picking",
    "fund-private-discretionary-long-value": "fund-private-equity-discretionary-stock-picking",
    "fund-private-discretionary-long-theme": "fund-private-equity-discretionary-stock-picking",
    "fund-private-quant-equity": "fund-private-equity-quant-long",
    "fund-private-quant-equity-multifactor": "fund-private-equity-quant-long-stock-selection",
    "fund-private-quant-equity-high-frequency": "fund-private-equity-quant-long-stock-selection",
    "fund-private-quant-equity-mid-frequency": "fund-private-equity-quant-long-stock-selection",
    "fund-private-quant-equity-low-frequency": "fund-private-equity-quant-long-stock-selection",
    "fund-private-market-neutral": "fund-private-equity-market-neutral",
    "fund-private-market-neutral-equity": "fund-private-equity-market-neutral",
    "fund-private-market-neutral-quant": "fund-private-equity-market-neutral",
    "fund-private-arbitrage": "fund-private-multi-asset-arbitrage",
    "fund-private-arbitrage-convertible": "fund-private-bond-convertible-trading",
    "fund-private-arbitrage-statistical": "fund-private-multi-asset-arbitrage",
    "fund-private-arbitrage-calendar": "fund-private-multi-asset-arbitrage",
    "fund-private-arbitrage-cross-commodity": "fund-private-multi-asset-arbitrage",
    "fund-private-arbitrage-futures-cash": "fund-private-multi-asset-arbitrage",
    "fund-private-cta": "fund-private-futures-derivatives",
    "fund-private-cta-quant": "fund-private-futures-derivatives-cta-quant",
    "fund-private-cta-quant-commodity": "fund-private-futures-derivatives-cta-quant-trend",
    "fund-private-cta-quant-financial": "fund-private-futures-derivatives-cta-quant-trend",
    "fund-private-cta-quant-multi": "fund-private-futures-derivatives-cta-quant-multi",
    "fund-private-cta-discretionary": "fund-private-futures-derivatives-cta-discretionary",
    "fund-private-macro": "fund-private-multi-asset-macro",
    "fund-private-macro-global": "fund-private-multi-asset-macro",
    "fund-private-macro-timing": "fund-private-multi-asset-macro",
    "fund-private-bond": "fund-private-bond",
    "fund-private-bond-credit": "fund-private-bond-enhanced",
    "fund-private-bond-rates": "fund-private-bond-pure",
    "fund-private-bond-convertible": "fund-private-bond-convertible-trading",
    "fund-private-multi-asset": "fund-private-multi-asset",
    "fund-private-multi-asset-multi-strategy": "fund-private-multi-asset-composite",
    "fund-private-multi-asset-fof": "fund-private-fund-of-funds-fof",
    "fund-private-options-vol": "fund-private-futures-derivatives-options",
    "fund-private-options-vol-arbitrage": "fund-private-futures-derivatives-options",
    "fund-private-options-vol-income": "fund-private-futures-derivatives-options",
}


def _serialize_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, ensure_ascii=False))
    return value


def _map_node_id(node_id: str | None, new_node_ids: set[str]) -> str | None:
    if node_id is None:
        return None
    normalized = str(node_id).strip()
    if not normalized:
        return None
    if normalized in new_node_ids:
        return normalized
    return OLD_NODE_ID_TO_NEW_NODE_ID.get(normalized)


def upgrade() -> None:
    from watchlist_app.reference_data.fund_taxonomy import fund_taxonomy_nodes
    from watchlist_app.services.fund_taxonomy import (
        build_taxonomy_context,
        merge_taxonomy_attributes,
    )

    bind = op.get_bind()
    node_table = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("asset_type", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_node_id", sa.String()),
        sa.column("level_index", sa.Integer()),
        sa.column("display_order", sa.Integer()),
        sa.column("is_leaf", sa.Boolean()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("path_node_ids_json", sa.JSON()),
    )
    assignment_table = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("asset_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
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

    new_nodes = fund_taxonomy_nodes()
    new_node_ids = {str(row["node_id"]) for row in new_nodes}
    assignment_rows = list(
        bind.execute(
            sa.select(
                assignment_table.c.asset_id,
                assignment_table.c.node_id,
            ).where(assignment_table.c.taxonomy_code == FUND_TAXONOMY_CODE)
        ).mappings()
    )
    mapped_node_by_asset = {
        str(row["asset_id"]): _map_node_id(
            str(row["node_id"]) if row["node_id"] is not None else None,
            new_node_ids,
        )
        for row in assignment_rows
    }

    bind.execute(
        sa.update(assignment_table)
        .where(assignment_table.c.taxonomy_code == FUND_TAXONOMY_CODE)
        .values(node_id=None)
    )
    bind.execute(
        sa.delete(node_table).where(node_table.c.taxonomy_code == FUND_TAXONOMY_CODE)
    )
    bind.execute(
        sa.insert(node_table),
        [{key: _serialize_json(value) for key, value in row.items()} for row in new_nodes],
    )

    for asset_id, node_id in mapped_node_by_asset.items():
        bind.execute(
            sa.update(assignment_table)
            .where(
                assignment_table.c.asset_id == asset_id,
                assignment_table.c.taxonomy_code == FUND_TAXONOMY_CODE,
            )
            .values(node_id=node_id)
        )

    taxonomy_by_asset: dict[str, dict[str, object]] = {}
    node_by_id = {str(row["node_id"]): row for row in new_nodes}
    empty_context = build_taxonomy_context(None)
    for asset_id, node_id in mapped_node_by_asset.items():
        taxonomy_by_asset[asset_id] = (
            build_taxonomy_context(node_by_id[node_id])
            if node_id is not None and node_id in node_by_id
            else empty_context
        )

    for row in bind.execute(sa.select(watchlist_row_table)).mappings():
        asset_id = str(row["asset_id"])
        next_attributes = merge_taxonomy_attributes(
            taxonomy_context=taxonomy_by_asset.get(asset_id, empty_context),
            instrument_attributes=(
                row["attributes_json"] if isinstance(row["attributes_json"], dict) else {}
            ),
        )
        bind.execute(
            sa.update(watchlist_row_table)
            .where(
                watchlist_row_table.c.watchlist_id == row["watchlist_id"],
                watchlist_row_table.c.asset_id == asset_id,
            )
            .values(attributes_json=_serialize_json(next_attributes))
        )

    for row in bind.execute(sa.select(summary_table)).mappings():
        asset_id = str(row["asset_id"])
        payload = row["payload_json"] if isinstance(row["payload_json"], dict) else {}
        bind.execute(
            sa.update(summary_table)
            .where(summary_table.c.asset_id == asset_id)
            .values(
                payload_json=_serialize_json(
                    {
                        **payload,
                        "taxonomy": taxonomy_by_asset.get(asset_id, empty_context),
                    }
                )
            )
        )


def downgrade() -> None:
    pass
