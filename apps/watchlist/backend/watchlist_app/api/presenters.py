from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.db.models.watchlists import (
    FieldCategory,
    FieldRegistry,
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
    Watchlist,
    WatchlistView,
)


TAXONOMY_GROUP_BY_CODE = "taxonomy"
GROUP_BY_FIELD_ORDER = (
    "management_firm_name",
    "overall_rating",
    "analyst_stance",
    "data_freshness_status",
)
GROUP_BY_FIELD_ORDER_INDEX = {
    field_key: index for index, field_key in enumerate(GROUP_BY_FIELD_ORDER)
}


def _local_view_id(record: WatchlistView) -> str:
    prefix = f"{record.watchlist_id}::"
    if record.watchlist_view_id.startswith(prefix):
        return record.watchlist_view_id[len(prefix) :]
    return record.watchlist_view_id


def present_watchlist(record: Watchlist) -> dict[str, object]:
    default_view = next((item for item in record.views if item.is_default), None)
    if default_view is None and record.views:
        default_view = record.views[0]
    return {
        "watchlist_id": record.watchlist_id,
        "name": record.name,
        "description": record.description,
        "item_count": len(record.items),
        "owner_type": record.owner_type,
        "owner_id": record.owner_id,
        "is_default": record.is_default,
        "is_shared": record.is_shared,
        "default_view_id": _local_view_id(default_view) if default_view else None,
    }


def present_watchlist_view(record: WatchlistView) -> dict[str, object]:
    return {
        "view_id": _local_view_id(record),
        "view_key": record.watchlist_view_id,
        "name": record.name,
        "description": record.description,
        "kind": record.kind,
        "default_group_by": record.default_group_by,
        "default_sort": record.default_sort_json,
        "default_filters": record.default_filters_json,
        "default_advanced_filters": record.default_advanced_filter_json or None,
        "columns": [
            column.field_key
            for column in sorted(record.columns, key=lambda item: item.display_order)
        ],
        "column_meta": [
            {
                "field_key": column.field_key,
                "display_order": column.display_order,
                "width": column.width,
                "is_visible": column.is_visible,
            }
            for column in sorted(record.columns, key=lambda item: item.display_order)
        ],
    }


def present_field_category(record: FieldCategory) -> dict[str, object]:
    return {
        "category_code": record.category_code,
        "label": record.label,
        "display_order": record.display_order,
        "parent_category_code": record.parent_category_code,
    }


def present_field_registry(record: FieldRegistry) -> dict[str, object]:
    return {
        "field_key": record.field_key,
        "label": record.label,
        "description": record.description,
        "category_code": record.category_code,
        "data_type": record.data_type,
        "formatter_code": record.formatter_code,
        "sort_mode": record.sort_mode,
        "filter_mode": record.filter_mode,
        "group_mode": record.group_mode,
        "instrument_scope_json": record.instrument_scope_json,
        "product_scope_json": record.product_scope_json,
        "availability_rule_json": record.availability_rule_json,
        "source_domain": record.source_domain,
        "source_metric_code": record.source_metric_code,
        "default_width": record.default_width,
        "default_visible": record.default_visible,
    }


def present_attribute_definition(record: InstrumentAttributeDefinition) -> dict[str, object]:
    return {
        "attribute_key": record.attribute_key,
        "label": record.label,
        "description": record.description,
        "data_type": record.data_type,
        "domain_code": record.domain_code,
        "group_code": record.group_code,
        "display_order": record.display_order,
        "options": record.options_json,
        "instrument_scope_json": record.instrument_scope_json,
        "applicability_json": record.applicability_json,
        "rubric_json": record.rubric_json,
        "is_groupable": record.is_groupable,
        "is_filterable": record.is_filterable,
        "is_view_column": record.is_view_column,
        "default_visible": record.default_visible,
        "required_for_monitoring": record.required_for_monitoring,
    }


def present_attribute_values(
    instrument_id: str,
    definitions: Sequence[InstrumentAttributeDefinition],
    values: Sequence[InstrumentAttributeValue],
) -> dict[str, object]:
    latest: dict[str, InstrumentAttributeValue] = {}
    for value in values:
        if value.attribute_key not in latest:
            latest[value.attribute_key] = value
    return {
        "instrument_id": instrument_id,
        "definitions": [present_attribute_definition(item) for item in definitions],
        "values": {key: item.value_json for key, item in latest.items()},
    }


def present_recalc_job(record: RecalcJob) -> dict[str, object]:
    return {
        "recalc_job_id": record.recalc_job_id,
        "job_type": record.job_type,
        "instrument_id": record.instrument_id,
        "trigger_type": record.trigger_type,
        "trigger_ref_type": record.trigger_ref_type,
        "trigger_ref_id": record.trigger_ref_id,
        "job_status": record.job_status,
        "priority": record.priority,
        "dedupe_key": record.dedupe_key,
        "payload_json": record.payload_json,
        "enqueued_at": record.enqueued_at.isoformat() if record.enqueued_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "error_message": record.error_message,
    }


def present_group_by_options(fields: Sequence[FieldRegistry]) -> list[dict[str, str]]:
    options = [{"code": "none", "label": "None"}]
    has_taxonomy_fields = any(
        field.field_key in {"attr.fund_regime", "attr.fund_taxonomy_level_1"}
        for field in fields
    )
    if has_taxonomy_fields:
        options.append({"code": TAXONOMY_GROUP_BY_CODE, "label": "Taxonomy"})
    groupable_fields = [
        field
        for field in fields
        if field.group_mode != "none"
        and field.field_key in GROUP_BY_FIELD_ORDER_INDEX
    ]
    for field in sorted(
        groupable_fields,
        key=lambda item: (
            GROUP_BY_FIELD_ORDER_INDEX[item.field_key],
            item.label,
        ),
    ):
        options.append({"code": field.field_key, "label": field.label})
    return options


def present_default_filter_summary(fields: Sequence[FieldRegistry]) -> dict[str, list[Any]]:
    summary: dict[str, list[Any]] = {}
    for field in fields:
        if field.default_visible and field.filter_mode != "none":
            summary[field.field_key] = []
    return summary
