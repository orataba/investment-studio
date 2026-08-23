"""Align taxonomy, Watchlist fields, and peer/research contracts.

Revision ID: 20260823_0045
Revises: 20260823_0044
"""

from __future__ import annotations

from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa

from watchlist_migration_snapshots.investment_watchlist_contract_0045 import (
    ATTRIBUTE_DEFINITION_ROWS,
    RESEARCH_SYSTEM_FIELD_ROWS,
)


revision = "20260823_0045"
down_revision = "20260823_0044"
branch_labels = None
depends_on = None


ALL_INVESTMENT_INSTRUMENT_TYPES = [
    "public_fund",
    "private_fund",
    "etf",
    "equity",
    "index",
]
REMOVED_COMPOSITE_PEER_FIELDS = {
    "attr.peer_overall_percentile",
    "attr.peer_return_percentile",
    "attr.peer_risk_percentile",
    "attr.peer_risk_adjusted_percentile",
}
REMOVED_COMPOSITE_PEER_ATTRIBUTES = {
    value.removeprefix("attr.") for value in REMOVED_COMPOSITE_PEER_FIELDS
}
EQUITY_SECTORS = (
    ("energy", "能源"),
    ("materials", "原材料"),
    ("industrials", "工业"),
    ("consumer-discretionary", "可选消费"),
    ("consumer-staples", "日常消费"),
    ("health-care", "医疗保健"),
    ("financials", "金融"),
    ("information-technology", "信息技术"),
    ("communication-services", "通信服务"),
    ("utilities", "公用事业"),
    ("real-estate", "房地产"),
)
EQUITY_MARKETS = (
    ("us", "美股", 1),
    ("hk", "港股", 2),
    ("cn-a", "A股", 3),
    ("eu", "欧洲股市", 4),
)
EQUITY_MARKET_BY_EXCHANGE = {
    "XNAS": "us",
    "XNYS": "us",
    "XASE": "us",
    "ARCX": "us",
    "BATS": "us",
    "XHKG": "hk",
    "XSHG": "cn-a",
    "XSHE": "cn-a",
    "XLON": "eu",
    "XETR": "eu",
    "XPAR": "eu",
    "XAMS": "eu",
    "XMIL": "eu",
    "XSWX": "eu",
}
INDEX_NODE_BY_INSTRUMENT_ID = {
    "h11001-csi": "index-fixed-income",
    "000852-sh": "index-broad-market",
    "932000-csi": "index-broad-market",
    "000300-sh": "index-broad-market",
    "000905-sh": "index-broad-market",
    "881001-wi": "index-broad-market",
}
RISK_FIELD_DESCRIPTIONS = {
    "max_drawdown": (
        "Since-inception maximum drawdown through this instrument's own latest "
        "observation, calculated from available canonical observations; inspect "
        "data quality alongside the metric."
    ),
    "attr.current_drawdown": (
        "Current drawdown through this instrument's own latest observation relative "
        "to its prior high watermark, calculated from available canonical observations."
    ),
    "volatility": (
        "Since-inception annualized volatility through this instrument's own latest "
        "observation, calculated from available canonical return intervals; inspect "
        "data quality alongside the metric."
    ),
    "sharpe_ratio": (
        "Since-inception zero-risk-free Sharpe ratio through this instrument's own "
        "latest observation, calculated from available canonical return intervals; "
        "inspect data quality alongside the metric."
    ),
}


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _remove_retired_fields(value: object) -> object | None:
    value = _json_value(value)
    retired = REMOVED_COMPOSITE_PEER_FIELDS | REMOVED_COMPOSITE_PEER_ATTRIBUTES
    if isinstance(value, str):
        return None if value in retired else value
    if isinstance(value, list):
        rewritten = [_remove_retired_fields(item) for item in value]
        return [item for item in rewritten if item is not None]
    if not isinstance(value, dict):
        return value
    if str(value.get("field") or "") in retired:
        return None
    rewritten: dict[str, object] = {}
    for key, item in value.items():
        if str(key) in retired:
            continue
        cleaned = _remove_retired_fields(item)
        if cleaned is not None:
            rewritten[str(key)] = cleaned
    return rewritten


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
    else:
        update_values = {
            key: value for key, value in values.items() if key != "created_at"
        }
        bind.execute(
            sa.update(table)
            .where(table.c[key_name] == key_value)
            .values(**update_values)
        )


def _attribute_field_row(definition: dict[str, object]) -> dict[str, object]:
    data_type = str(definition["data_type"])
    formatter_code = "text"
    sort_mode = "alpha"
    filter_mode = "multi_select"
    group_mode = "discrete"
    if data_type == "boolean":
        formatter_code = "boolean"
    elif data_type == "number":
        formatter_code = "decimal"
        sort_mode = "numeric"
        filter_mode = "range"
        group_mode = "none"
    elif data_type == "date":
        formatter_code = "date"
        sort_mode = "date"
        filter_mode = "date_range"
        group_mode = "none"
    elif data_type == "multi_select":
        formatter_code = "tags"
        group_mode = "none"
    if not bool(definition.get("is_groupable", True)):
        group_mode = "none"
    domain_code = str(definition.get("domain_code") or "research")
    category_code = {
        "overview": "basics",
        "research": "research_framework",
        "monitoring": "monitoring_assessment",
    }.get(domain_code, "research_framework")
    attribute_key = str(definition["attribute_key"])
    return {
        "field_key": f"attr.{attribute_key}",
        "label": str(definition["label"]),
        "description": definition.get("description"),
        "category_code": category_code,
        "data_type": data_type,
        "formatter_code": formatter_code,
        "sort_mode": sort_mode,
        "filter_mode": filter_mode,
        "group_mode": group_mode,
        "instrument_scope_json": list(definition["instrument_scope_json"]),
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_attribute_value"]},
        "source_domain": "custom_attribute",
        "source_metric_code": f"instrument_attribute_value.{attribute_key}",
        "default_width": 160,
        "default_visible": bool(definition.get("default_visible", False)),
    }


def _taxonomy_node_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for market_code, market_label, market_order in EQUITY_MARKETS:
        root_id = f"equity-market-{market_code}"
        rows.append(
            {
                "node_id": root_id,
                "taxonomy_code": "instrument_taxonomy",
                "instrument_type": "equity",
                "label": market_label,
                "parent_node_id": None,
                "level_index": 1,
                "display_order": market_order,
                "is_leaf": False,
                "path_labels_json": [market_label],
                "path_node_ids_json": [root_id],
            }
        )
        for sector_order, (sector_code, sector_label) in enumerate(
            EQUITY_SECTORS,
            start=1,
        ):
            node_id = f"equity-{market_code}-{sector_code}"
            rows.append(
                {
                    "node_id": node_id,
                    "taxonomy_code": "instrument_taxonomy",
                    "instrument_type": "equity",
                    "label": sector_label,
                    "parent_node_id": root_id,
                    "level_index": 2,
                    "display_order": sector_order,
                    "is_leaf": True,
                    "path_labels_json": [market_label, sector_label],
                    "path_node_ids_json": [root_id, node_id],
                }
            )
        unclassified_id = f"equity-{market_code}-unclassified"
        rows.append(
            {
                "node_id": unclassified_id,
                "taxonomy_code": "instrument_taxonomy",
                "instrument_type": "equity",
                "label": "待分类",
                "parent_node_id": root_id,
                "level_index": 2,
                "display_order": len(EQUITY_SECTORS) + 1,
                "is_leaf": True,
                "path_labels_json": [market_label, "待分类"],
                "path_node_ids_json": [root_id, unclassified_id],
            }
        )
    return rows


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    now = datetime.now(UTC).replace(microsecond=0)

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
    view = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("default_sort_json", sa.JSON()),
        sa.column("default_filters_json", sa.JSON()),
        sa.column("default_advanced_filter_json", sa.JSON()),
        sa.column("default_group_by", sa.String()),
    )
    view_column = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_column_id", sa.Integer()),
        sa.column("field_key", sa.String()),
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

    bind.execute(
        sa.delete(view_column).where(
            view_column.c.field_key.in_(REMOVED_COMPOSITE_PEER_FIELDS)
        )
    )
    for row in bind.execute(sa.select(view)).mappings():
        updates: dict[str, object] = {}
        for column_name in (
            "default_sort_json",
            "default_filters_json",
            "default_advanced_filter_json",
        ):
            current = row[column_name]
            rewritten = _remove_retired_fields(current)
            if rewritten != _json_value(current):
                updates[column_name] = rewritten or ([] if column_name == "default_sort_json" else {})
        if str(row["default_group_by"] or "") in REMOVED_COMPOSITE_PEER_FIELDS:
            updates["default_group_by"] = "none"
        if updates:
            bind.execute(
                sa.update(view)
                .where(view.c.watchlist_view_id == row["watchlist_view_id"])
                .values(**updates)
            )

    for table, key_columns, payload_column in (
        (watchlist_row, (watchlist_row.c.watchlist_id, watchlist_row.c.instrument_id), watchlist_row.c.attributes_json),
        (summary, (summary.c.instrument_id,), summary.c.payload_json),
    ):
        for row in bind.execute(sa.select(*key_columns, payload_column)).mappings():
            current = row[payload_column.name]
            rewritten = _remove_retired_fields(current)
            if rewritten == _json_value(current):
                continue
            predicate = sa.and_(
                *(column == row[column.name] for column in key_columns)
            )
            bind.execute(
                sa.update(table)
                .where(predicate)
                .values({payload_column.name: rewritten or {}})
            )

    bind.execute(
        sa.delete(field).where(field.c.field_key.in_(REMOVED_COMPOSITE_PEER_FIELDS))
    )
    bind.execute(
        sa.update(field)
        .where(field.c.category_code == "performance_risk")
        .values(
            instrument_scope_json=ALL_INVESTMENT_INSTRUMENT_TYPES,
            product_scope_json=[],
        )
    )
    bind.execute(
        sa.update(field)
        .where(field.c.field_key == "last_nav_date")
        .values(
            instrument_scope_json=ALL_INVESTMENT_INSTRUMENT_TYPES,
            product_scope_json=[],
        )
    )
    bind.execute(
        sa.update(field)
        .where(
            field.c.source_domain == "custom_attribute",
            field.c.data_type.not_in(("single_select", "boolean", "text")),
        )
        .values(group_mode="none")
    )
    for field_key, description in RISK_FIELD_DESCRIPTIONS.items():
        bind.execute(
            sa.update(field)
            .where(field.c.field_key == field_key)
            .values(description=description)
        )

    for raw_definition in ATTRIBUTE_DEFINITION_ROWS:
        definition_row = {
            "attribute_key": raw_definition["attribute_key"],
            "label": raw_definition["label"],
            "description": raw_definition.get("description"),
            "data_type": raw_definition["data_type"],
            "domain_code": raw_definition["domain_code"],
            "group_code": raw_definition["group_code"],
            "display_order": raw_definition["display_order"],
            "options_json": list(raw_definition.get("options") or []),
            "instrument_scope_json": list(raw_definition["instrument_scope_json"]),
            "applicability_json": dict(raw_definition.get("applicability_json") or {}),
            "rubric_json": dict(raw_definition.get("rubric_json") or {}),
            "is_groupable": bool(raw_definition.get("is_groupable", True)),
            "is_filterable": bool(raw_definition.get("is_filterable", True)),
            "is_view_column": bool(raw_definition.get("is_view_column", True)),
            "default_visible": bool(raw_definition.get("default_visible", False)),
            "required_for_monitoring": bool(
                raw_definition.get("required_for_monitoring", False)
            ),
            "created_at": now,
        }
        _upsert_row(
            bind,
            definition,
            key_name="attribute_key",
            values=definition_row,
        )
        _upsert_row(
            bind,
            field,
            key_name="field_key",
            values=_attribute_field_row(raw_definition),
        )
    for system_field_row in RESEARCH_SYSTEM_FIELD_ROWS:
        _upsert_row(
            bind,
            field,
            key_name="field_key",
            values=dict(system_field_row),
        )

    bind.execute(
        sa.update(definition)
        .where(definition.c.data_type.not_in(("single_select", "boolean", "text")))
        .values(is_groupable=False)
    )
    valid_discrete_fields = set(
        bind.execute(
            sa.select(field.c.field_key).where(field.c.group_mode == "discrete")
        ).scalars()
    )
    for row in bind.execute(
        sa.select(view.c.watchlist_view_id, view.c.default_group_by)
    ).mappings():
        group_by = str(row["default_group_by"] or "none")
        if group_by in {"none", "instrument_type", "taxonomy", "data_freshness_status"}:
            continue
        if group_by not in valid_discrete_fields:
            bind.execute(
                sa.update(view)
                .where(view.c.watchlist_view_id == row["watchlist_view_id"])
                .values(default_group_by="none")
            )

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
    detail = sa.table(
        "instrument_detail",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("metadata_json", sa.JSON()),
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
    taxonomy_rows = _taxonomy_node_rows()
    taxonomy_by_id = {str(row["node_id"]): row for row in taxonomy_rows}
    for taxonomy_row in taxonomy_rows:
        _upsert_row(
            bind,
            node,
            key_name="node_id",
            values=taxonomy_row,
        )

    old_node_parent_by_id = {
        str(row["node_id"]): str(row["parent_node_id"] or "")
        for row in bind.execute(
            sa.select(node.c.node_id, node.c.parent_node_id).where(
                node.c.node_id.like("equity-exchange-%")
            )
        ).mappings()
    }
    current_assignment_by_instrument = {
        str(row["instrument_id"]): str(row["node_id"] or "")
        for row in bind.execute(
            sa.select(assignment.c.instrument_id, assignment.c.node_id).where(
                assignment.c.taxonomy_code == "instrument_taxonomy"
            )
        ).mappings()
    }
    for row in bind.execute(
        sa.select(detail.c.instrument_id, detail.c.metadata_json).where(
            detail.c.instrument_type == "equity"
        )
    ).mappings():
        instrument_id = str(row["instrument_id"])
        metadata = _json_value(row["metadata_json"])
        exchange_code = (
            str(metadata.get("exchange_code") or "").strip().upper()
            if isinstance(metadata, dict)
            else ""
        )
        market_code = EQUITY_MARKET_BY_EXCHANGE.get(exchange_code)
        current_node_id = current_assignment_by_instrument.get(instrument_id, "")
        if market_code is None:
            parent_node_id = old_node_parent_by_id.get(current_node_id, "")
            if parent_node_id.startswith("equity-market-"):
                market_code = parent_node_id.removeprefix("equity-market-")
        if market_code is None:
            raise RuntimeError(
                "Equity taxonomy migration requires a supported exchange identity: "
                f"{instrument_id}:{exchange_code or current_node_id or 'missing'}"
            )
        target_node_id = f"equity-{market_code}-unclassified"
        target = taxonomy_by_id[target_node_id]
        if current_node_id == target_node_id:
            continue
        values = {
            "node_id": target_node_id,
            "assigned_at": now,
            "source_record_id": f"migration:20260823_0045:{exchange_code or market_code}",
        }
        if current_node_id:
            bind.execute(
                sa.update(assignment)
                .where(
                    assignment.c.instrument_id == instrument_id,
                    assignment.c.taxonomy_code == "instrument_taxonomy",
                )
                .values(**values)
            )
        else:
            bind.execute(
                sa.insert(assignment).values(
                    instrument_id=instrument_id,
                    taxonomy_code="instrument_taxonomy",
                    **values,
                )
            )
        bind.execute(
            sa.insert(history).values(
                instrument_id=instrument_id,
                taxonomy_code="instrument_taxonomy",
                node_id=target_node_id,
                path_labels_json=list(target["path_labels_json"]),
                assigned_at=now,
                source_record_id=values["source_record_id"],
            )
        )

    bind.execute(
        sa.delete(node).where(node.c.node_id.like("equity-exchange-%"))
    )
    for instrument_id, target_node_id in INDEX_NODE_BY_INSTRUMENT_ID.items():
        if bind.execute(
            sa.select(detail.c.instrument_id).where(
                detail.c.instrument_id == instrument_id,
                detail.c.instrument_type == "index",
            )
        ).first() is None:
            continue
        current_node_id = current_assignment_by_instrument.get(instrument_id, "")
        if current_node_id == target_node_id:
            continue
        source_record_id = "migration:20260823_0045:index-classification"
        values = {
            "node_id": target_node_id,
            "assigned_at": now,
            "source_record_id": source_record_id,
        }
        if current_node_id:
            bind.execute(
                sa.update(assignment)
                .where(
                    assignment.c.instrument_id == instrument_id,
                    assignment.c.taxonomy_code == "instrument_taxonomy",
                )
                .values(**values)
            )
        else:
            bind.execute(
                sa.insert(assignment).values(
                    instrument_id=instrument_id,
                    taxonomy_code="instrument_taxonomy",
                    **values,
                )
            )
        target = bind.execute(
            sa.select(node.c.path_labels_json).where(node.c.node_id == target_node_id)
        ).scalar_one()
        bind.execute(
            sa.insert(history).values(
                instrument_id=instrument_id,
                taxonomy_code="instrument_taxonomy",
                node_id=target_node_id,
                path_labels_json=list(_json_value(target) or []),
                assigned_at=now,
                source_record_id=source_record_id,
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Taxonomy assignments and investment research fields are now active data. "
        "Restore a pre-migration database backup instead of recreating retired contracts."
    )
