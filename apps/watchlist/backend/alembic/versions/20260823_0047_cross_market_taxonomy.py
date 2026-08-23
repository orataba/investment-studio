"""Replace listing-relative taxonomy with cross-market investment categories.

Revision ID: 20260823_0047
Revises: 20260823_0046
"""

from __future__ import annotations

from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa

from watchlist_migration_snapshots.cross_market_taxonomy_0047 import (
    PRIMARY_GEOGRAPHIC_EXPOSURE_DEFINITION,
    TAXONOMY_NODE_ROWS,
)


revision = "20260823_0047"
down_revision = "20260823_0046"
branch_labels = None
depends_on = None


LEGACY_ASSIGNMENT_MAP = {
    # Public funds: wrapper/domicile labels become strategy categories.
    "fund-public-equity": "fund-public-equity-active",
    "fund-public-equity-standard": "fund-public-equity-active",
    "fund-public-equity-indexed": "fund-public-equity-index",
    "fund-public-hybrid": "fund-public-allocation-flexible",
    "fund-public-hybrid-equity-biased": "fund-public-allocation-equity-biased",
    "fund-public-hybrid-flexible": "fund-public-allocation-flexible",
    "fund-public-hybrid-balanced": "fund-public-allocation-balanced",
    "fund-public-hybrid-bond-biased": "fund-public-allocation-bond-biased",
    "fund-public-hybrid-strategy": "fund-public-alternative-multi-strategy",
    "fund-public-bond": "fund-public-fixed-income-aggregate",
    "fund-public-bond-pure": "fund-public-fixed-income-aggregate",
    "fund-public-bond-ordinary": "fund-public-fixed-income-aggregate",
    "fund-public-bond-convertible": "fund-public-fixed-income-convertible",
    "fund-public-bond-indexed": "fund-public-fixed-income-index",
    "fund-public-bond-cd": "fund-public-money-market",
    "fund-public-qdii": "fund-public-allocation-flexible",
    "fund-public-qdii-reit": "fund-public-real-assets-real-estate",
    "fund-public-qdii-equity": "fund-public-equity-active",
    "fund-public-qdii-hybrid": "fund-public-allocation-flexible",
    "fund-public-qdii-commodity": "fund-public-commodity-broad",
    "fund-public-qdii-bond": "fund-public-fixed-income-aggregate",
    "fund-public-commodity": "fund-public-commodity-broad",
    "fund-public-commodity-other": "fund-public-commodity-broad",
    "fund-public-reits": "fund-public-real-assets-real-estate",
    "fund-public-fof": "fund-public-fof-allocation",
    "fund-public-fof-bond": "fund-public-fof-fixed-income",
    "fund-public-fof-hybrid": "fund-public-fof-allocation",
    "fund-public-fof-pension": "fund-public-fof-allocation",
    # Private funds: benchmark names leave taxonomy and remain research/reference facts.
    "fund-private-equity": "fund-private-equity-discretionary-long",
    "fund-private-equity-discretionary-stock-picking": "fund-private-equity-discretionary-long",
    "fund-private-equity-discretionary-private-placement": "fund-private-equity-discretionary-long",
    "fund-private-equity-quant-long": "fund-private-equity-quant-stock-selection",
    "fund-private-equity-quant-long-300": "fund-private-equity-quant-index-enhanced",
    "fund-private-equity-quant-long-500": "fund-private-equity-quant-index-enhanced",
    "fund-private-equity-quant-long-1000": "fund-private-equity-quant-index-enhanced",
    "fund-private-equity-quant-long-2000": "fund-private-equity-quant-index-enhanced",
    "fund-private-equity-quant-long-dividend": "fund-private-equity-quant-index-enhanced",
    "fund-private-equity-quant-long-stock-selection": "fund-private-equity-quant-stock-selection",
    "fund-private-equity-quant-long-other-enhanced": "fund-private-equity-quant-index-enhanced",
    "fund-private-bond": "fund-private-credit-long-only",
    "fund-private-bond-pure": "fund-private-credit-long-only",
    "fund-private-bond-enhanced": "fund-private-credit-long-only",
    "fund-private-bond-composite": "fund-private-credit-long-only",
    "fund-private-bond-convertible-trading": "fund-private-relative-value-convertible",
    "fund-private-futures-derivatives": "fund-private-managed-futures-multi",
    "fund-private-futures-derivatives-cta-discretionary": "fund-private-managed-futures-multi",
    "fund-private-futures-derivatives-cta-discretionary-trend": "fund-private-managed-futures-trend",
    "fund-private-futures-derivatives-cta-discretionary-arbitrage": "fund-private-managed-futures-relative-value",
    "fund-private-futures-derivatives-cta-discretionary-multi": "fund-private-managed-futures-multi",
    "fund-private-futures-derivatives-cta-quant": "fund-private-managed-futures-multi",
    "fund-private-futures-derivatives-cta-quant-trend": "fund-private-managed-futures-trend",
    "fund-private-futures-derivatives-cta-quant-arbitrage": "fund-private-managed-futures-relative-value",
    "fund-private-futures-derivatives-cta-quant-multi": "fund-private-managed-futures-multi",
    "fund-private-futures-derivatives-options": "fund-private-relative-value-volatility",
    "fund-private-futures-derivatives-other": "fund-private-managed-futures-multi",
    "fund-private-multi-asset": "fund-private-multi-strategy",
    "fund-private-multi-asset-macro": "fund-private-macro-discretionary",
    "fund-private-multi-asset-arbitrage": "fund-private-relative-value-multi-asset",
    "fund-private-multi-asset-composite": "fund-private-multi-strategy",
    "fund-private-fund-of-funds": "fund-private-fund-of-funds-fof",
    # ETFs and indexes: geography is a separate peer dimension.
    "etf-equity": "etf-equity-broad-market",
    "etf-equity-index": "etf-equity-broad-market",
    "etf-equity-sector-theme": "etf-equity-theme",
    "etf-equity-strategy": "etf-equity-factor",
    "etf-equity-cross-border": "etf-equity-broad-market",
    "etf-fixed-income": "etf-fixed-income-aggregate",
    "etf-fixed-income-credit": "etf-fixed-income-investment-grade",
    "etf-fixed-income-cash": "etf-cash",
    "etf-commodity": "etf-commodity-broad",
    "etf-commodity-other": "etf-commodity-broad",
    "etf-multi-asset": "etf-multi-asset-allocation",
    "index-broad-market": "index-equity-broad-market",
    "index-sector-theme": "index-equity-theme",
    "index-strategy": "index-equity-factor",
    "index-fixed-income": "index-fixed-income-aggregate",
    "index-commodity": "index-commodity-broad",
    "index-multi-asset": "index-multi-asset-allocation",
}


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _upsert_row(
    bind: sa.Connection,
    table: sa.TableClause,
    *,
    key_name: str,
    values: dict[str, object],
) -> None:
    key_value = values[key_name]
    exists = bind.execute(
        sa.select(table.c[key_name]).where(table.c[key_name] == key_value)
    ).first()
    if exists is None:
        bind.execute(sa.insert(table).values(**values))
        return
    bind.execute(
        sa.update(table)
        .where(table.c[key_name] == key_value)
        .values(**{key: value for key, value in values.items() if key != "created_at"})
    )


def _taxonomy_context(row: dict[str, object]) -> dict[str, object]:
    path_labels = list(row["path_labels_json"])
    derived_values = {
        "instrument_taxonomy_leaf": path_labels[-1],
        "instrument_taxonomy_path": " / ".join(path_labels),
        **{
            f"instrument_taxonomy_level_{level}": label
            for level, label in enumerate(path_labels, start=1)
        },
    }
    return {
        "taxonomy_code": "instrument_taxonomy",
        "assigned_node_id": row["node_id"],
        "assigned_label": row["label"],
        "path_labels": path_labels,
        "path_node_ids": list(row["path_node_ids_json"]),
        "depth": len(path_labels),
        "derived_values": derived_values,
    }


def _replace_taxonomy_attributes(
    attributes: object,
    taxonomy_context: dict[str, object],
) -> dict[str, object]:
    current = dict(attributes) if isinstance(attributes, dict) else {}
    for key in list(current):
        if key == "instrument_taxonomy_leaf" or key == "instrument_taxonomy_path" or key.startswith(
            "instrument_taxonomy_level_"
        ):
            current.pop(key, None)
    current.update(dict(taxonomy_context["derived_values"]))
    return current


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    now = datetime.now(UTC).replace(microsecond=0)

    node = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("label", sa.String()),
        sa.column("parent_node_id", sa.String()),
        sa.column("level_index", sa.Integer()),
        sa.column("display_order", sa.Integer()),
        sa.column("is_leaf", sa.Boolean()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("path_node_ids_json", sa.JSON()),
    )
    assignment = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
    )
    history = sa.table(
        "instrument_taxonomy_assignment_history",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
    )
    watchlist_row = sa.table(
        "watchlist_row_read_model",
        sa.column("watchlist_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("attributes_json", sa.JSON()),
    )
    summary = sa.table(
        "instrument_summary_read_model",
        sa.column("instrument_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
    )

    new_by_id = {str(row["node_id"]): dict(row) for row in TAXONOMY_NODE_ROWS}
    new_leaf_ids = {
        node_id for node_id, row in new_by_id.items() if bool(row["is_leaf"])
    }
    for taxonomy_row in TAXONOMY_NODE_ROWS:
        _upsert_row(bind, node, key_name="node_id", values=dict(taxonomy_row))

    assignments = list(
        bind.execute(
            sa.select(assignment).where(
                assignment.c.taxonomy_code == "instrument_taxonomy"
            )
        ).mappings()
    )
    for current in assignments:
        instrument_id = str(current["instrument_id"])
        current_node_id = str(current["node_id"] or "")
        if not current_node_id:
            continue
        if current_node_id in new_leaf_ids:
            target_node_id = current_node_id
        else:
            target_node_id = LEGACY_ASSIGNMENT_MAP.get(current_node_id)
            if target_node_id is None:
                raise RuntimeError(
                    "Cross-market taxonomy migration has no explicit mapping for "
                    f"{instrument_id}:{current_node_id}"
                )
        target = new_by_id[target_node_id]
        if target_node_id != current_node_id:
            source_record_id = "migration:20260823_0047:cross-market-taxonomy"
            bind.execute(
                sa.update(assignment)
                .where(
                    assignment.c.instrument_id == instrument_id,
                    assignment.c.taxonomy_code == "instrument_taxonomy",
                )
                .values(
                    node_id=target_node_id,
                    assigned_at=now,
                    source_record_id=source_record_id,
                )
            )
            bind.execute(
                sa.insert(history).values(
                    instrument_id=instrument_id,
                    taxonomy_code="instrument_taxonomy",
                    node_id=target_node_id,
                    path_labels_json=list(target["path_labels_json"]),
                    assigned_at=now,
                    source_record_id=source_record_id,
                )
            )

        context = _taxonomy_context(target)
        for row in bind.execute(
            sa.select(watchlist_row).where(
                watchlist_row.c.instrument_id == instrument_id
            )
        ).mappings():
            bind.execute(
                sa.update(watchlist_row)
                .where(
                    watchlist_row.c.watchlist_id == row["watchlist_id"],
                    watchlist_row.c.instrument_id == instrument_id,
                )
                .values(
                    attributes_json=_replace_taxonomy_attributes(
                        _json_value(row["attributes_json"]), context
                    )
                )
            )
        summary_row = bind.execute(
            sa.select(summary).where(summary.c.instrument_id == instrument_id)
        ).mappings().first()
        if summary_row is not None:
            payload = _json_value(summary_row["payload_json"])
            payload = dict(payload) if isinstance(payload, dict) else {}
            payload["taxonomy"] = context
            payload["instrument_attributes"] = _replace_taxonomy_attributes(
                payload.get("instrument_attributes"), context
            )
            bind.execute(
                sa.update(summary)
                .where(summary.c.instrument_id == instrument_id)
                .values(payload_json=payload)
            )

    existing_replaced_ids = {
        str(value)
        for value in bind.execute(
            sa.select(node.c.node_id).where(
                node.c.taxonomy_code == "instrument_taxonomy",
                node.c.instrument_type.in_(("public_fund", "private_fund", "etf", "index")),
            )
        ).scalars()
        if str(value) not in new_by_id
    }
    if existing_replaced_ids:
        bind.execute(sa.delete(node).where(node.c.node_id.in_(existing_replaced_ids)))

    definition = sa.table(
        "instrument_attribute_definition",
        sa.column("attribute_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("data_type", sa.String()),
        sa.column("domain_code", sa.String()),
        sa.column("group_code", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("options_json", sa.JSON()),
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("applicability_json", sa.JSON()),
        sa.column("rubric_json", sa.JSON()),
        sa.column("is_groupable", sa.Boolean()),
        sa.column("is_filterable", sa.Boolean()),
        sa.column("is_view_column", sa.Boolean()),
        sa.column("default_visible", sa.Boolean()),
        sa.column("required_for_monitoring", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    field = sa.table(
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
        sa.column("instrument_scope_json", sa.JSON()),
        sa.column("product_scope_json", sa.JSON()),
        sa.column("availability_rule_json", sa.JSON()),
        sa.column("source_domain", sa.String()),
        sa.column("source_metric_code", sa.String()),
        sa.column("default_width", sa.Integer()),
        sa.column("default_visible", sa.Boolean()),
    )
    raw = dict(PRIMARY_GEOGRAPHIC_EXPOSURE_DEFINITION)
    definition_values = {
        "attribute_key": raw["attribute_key"],
        "label": raw["label"],
        "description": raw["description"],
        "data_type": raw["data_type"],
        "domain_code": raw["domain_code"],
        "group_code": raw["group_code"],
        "display_order": raw["display_order"],
        "options_json": list(raw["options"]),
        "instrument_scope_json": list(raw["instrument_scope_json"]),
        "applicability_json": {},
        "rubric_json": dict(raw["rubric_json"]),
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
        "required_for_monitoring": True,
        "created_at": now,
    }
    _upsert_row(
        bind,
        definition,
        key_name="attribute_key",
        values=definition_values,
    )
    _upsert_row(
        bind,
        field,
        key_name="field_key",
        values={
            "field_key": "attr.primary_geographic_exposure",
            "label": raw["label"],
            "description": raw["description"],
            "category_code": "basics",
            "data_type": "single_select",
            "formatter_code": "text",
            "sort_mode": "alpha",
            "filter_mode": "multi_select",
            "group_mode": "discrete",
            "instrument_scope_json": list(raw["instrument_scope_json"]),
            "product_scope_json": [],
            "availability_rule_json": {"requires": ["instrument_attribute_value"]},
            "source_domain": "custom_attribute",
            "source_metric_code": "instrument_attribute_value.primary_geographic_exposure",
            "default_width": 160,
            "default_visible": False,
        },
    )
    bind.execute(
        sa.update(definition)
        .where(definition.c.attribute_key.in_(("style_profile", "portfolio_construction")))
        .values(applicability_json={})
    )


def downgrade() -> None:
    raise RuntimeError(
        "Cross-market taxonomy assignments are active investment data. "
        "Restore a pre-migration database backup instead of recreating listing-relative categories."
    )
