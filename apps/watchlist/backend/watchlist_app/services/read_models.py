from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from watchlist_app.db.models.read_models import InstrumentChartReadModel, WatchlistRowReadModel
from watchlist_app.db.models.watchlists import InstrumentAttributeValue, WatchlistView


TAXONOMY_GROUP_BY_CODE = "taxonomy"
TAXONOMY_GROUP_FIELDS = [
    "attr.fund_regime",
    "attr.fund_taxonomy_level_1",
    "attr.fund_taxonomy_level_2",
    "attr.fund_taxonomy_level_3",
    "attr.fund_taxonomy_level_4",
    "attr.fund_taxonomy_level_5",
    "attr.fund_taxonomy_level_6",
]


def _serialize_scalar(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def serialize_payload(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): serialize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_payload(item) for item in value]
    return _serialize_scalar(value)


def collapse_latest_attribute_values(
    values: Sequence[InstrumentAttributeValue],
) -> dict[str, object]:
    latest: dict[str, object] = {}
    for value in values:
        if value.attribute_key not in latest:
            latest[value.attribute_key] = value.value_json
    return latest


def watchlist_row_to_dict(record: WatchlistRowReadModel) -> dict[str, object]:
    return {
        "watchlist_id": record.watchlist_id,
        "instrument_id": record.instrument_id,
        "instrument_type": record.instrument_type,
        "instrument_name": record.instrument_name,
        "share_class": record.share_class,
        "ticker_or_isin": record.ticker_or_isin,
        "management_firm_name": record.management_firm_name,
        "research_rating": _serialize_scalar(record.research_rating),
        "research_rating_as_of": _serialize_scalar(record.research_rating_as_of),
        "research_rating_updated_at": _serialize_scalar(record.research_rating_updated_at),
        "return_ytd": _serialize_scalar(record.return_ytd),
        "return_1w": _serialize_scalar(record.return_1w),
        "return_mtd": _serialize_scalar(record.return_mtd),
        "return_1m": _serialize_scalar(record.return_1m),
        "return_1y": _serialize_scalar(record.return_1y),
        "annualized_return": _serialize_scalar(record.annualized_return),
        "return_3y": _serialize_scalar(record.return_3y),
        "return_5y": _serialize_scalar(record.return_5y),
        "max_drawdown": _serialize_scalar(record.max_drawdown),
        "volatility": _serialize_scalar(record.volatility),
        "sharpe_ratio": _serialize_scalar(record.sharpe_ratio),
        "attributes": serialize_payload(record.attributes_json),
        "last_nav_date": _serialize_scalar(record.last_nav_date),
        "data_freshness_status": record.data_freshness_status,
        "market_data_input_watermark_at": _serialize_scalar(
            record.market_data_input_watermark_at
        ),
        "last_recalculated_at": _serialize_scalar(record.last_recalculated_at),
        "last_successful_snapshot_at": _serialize_scalar(
            record.last_successful_snapshot_at
        ),
        "staleness_reason": record.staleness_reason,
    }


def _extract_latest_quote(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    calculation_state = payload.get("calculation_state")
    if (
        not isinstance(calculation_state, dict)
        or calculation_state.get("current_endpoint_state") != "resolved"
    ):
        return None
    endpoint_observation_date = calculation_state.get(
        "current_endpoint_observation_date"
    )
    if not isinstance(endpoint_observation_date, str) or not endpoint_observation_date:
        return None
    series = payload.get("series")
    if not isinstance(series, list):
        return None
    for candidate in series:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("role") != "chart":
            continue
        points = candidate.get("points")
        if not isinstance(points, list) or not points:
            continue
        latest_point = points[-1]
        if not isinstance(latest_point, dict):
            continue
        value = latest_point.get("value")
        quote_date = latest_point.get("date")
        if (
            not isinstance(quote_date, str)
            or quote_date[:10] != endpoint_observation_date[:10]
        ):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return {
            "latest_quote": float(value),
            "latest_quote_date": quote_date[:10] if isinstance(quote_date, str) and quote_date else None,
        }
    return None


def build_latest_quote_overrides(
    charts: Sequence[InstrumentChartReadModel] | None,
) -> dict[str, dict[str, object]]:
    overrides: dict[str, dict[str, object]] = {}
    for chart in charts or []:
        latest_quote = _extract_latest_quote(chart.payload_json)
        if latest_quote is not None:
            overrides[chart.instrument_id] = latest_quote
    return overrides


CHART_FIELD_POINT_LIMITS = {
    "price_chart_1d": 10,
    "price_chart_1w": 20,
    "price_chart_1m": 40,
    "price_chart_1y": 64,
}
DEFAULT_SPARKLINE_POINT_LIMIT = 40


def _is_chart_field(field_key: object) -> bool:
    normalized = str(field_key or "").strip().lower()
    return normalized.startswith("price_chart_") or "sparkline" in normalized


def _sparkline_point_limit(selected_fields: Sequence[object]) -> int:
    chart_fields = [str(field) for field in selected_fields if _is_chart_field(field)]
    if not chart_fields:
        return 0
    return max(
        CHART_FIELD_POINT_LIMITS.get(field, DEFAULT_SPARKLINE_POINT_LIMIT)
        for field in chart_fields
    )


def _extract_sparkline_points(
    payload: object,
    *,
    point_limit: int,
) -> list[dict[str, object]]:
    if point_limit <= 0 or not isinstance(payload, dict):
        return []
    series = payload.get("series")
    if not isinstance(series, list):
        return []
    for candidate in series:
        if not isinstance(candidate, dict):
            continue
        points = candidate.get("points")
        if not isinstance(points, list):
            continue
        normalized: list[dict[str, object]] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            value = point.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            normalized.append(
                {
                    "date": point.get("date"),
                    "value": float(value),
                }
            )
        if normalized:
            return normalized[-point_limit:]
    return []


def build_sparkline_payload(
    charts: Sequence[InstrumentChartReadModel] | None,
    *,
    instrument_ids: Sequence[str],
    selected_fields: Sequence[object],
) -> dict[str, list[dict[str, object]]]:
    point_limit = _sparkline_point_limit(selected_fields)
    if point_limit <= 0:
        return {}
    requested_ids = set(instrument_ids)
    payload: dict[str, list[dict[str, object]]] = {}
    for chart in charts or []:
        if chart.instrument_id not in requested_ids:
            continue
        points = _extract_sparkline_points(
            chart.payload_json,
            point_limit=point_limit,
        )
        if points:
            payload[chart.instrument_id] = points
    return payload


def build_watchlist_row_materialization(
    *,
    watchlist_id: str,
    instrument_id: str,
    instrument_type: str,
    source_row: WatchlistRowReadModel | None,
    display_name: str | None,
    share_class: str | None,
    ticker_or_isin: str | None,
    management_firm_name: str | None,
    research_rating: int | None,
    research_rating_as_of: date | None,
    research_rating_updated_at: datetime | None,
    attributes: dict[str, object],
    freshness_status: str,
    market_data_input_watermark_at: datetime | None,
    last_recalculated_at: datetime | None,
    last_successful_snapshot_at: datetime | None,
    staleness_reason: str | None,
) -> dict[str, object]:
    if source_row is None:
        payload: dict[str, object] = {
            "instrument_name": display_name or instrument_id.upper(),
            "share_class": share_class,
            "ticker_or_isin": ticker_or_isin,
            "management_firm_name": management_firm_name,
            "research_rating": research_rating,
            "research_rating_as_of": research_rating_as_of,
            "research_rating_updated_at": research_rating_updated_at,
            "return_ytd": None,
            "return_1w": None,
            "return_mtd": None,
            "return_1m": None,
            "return_1y": None,
            "annualized_return": None,
            "return_3y": None,
            "return_5y": None,
            "max_drawdown": None,
            "volatility": None,
            "sharpe_ratio": None,
            "last_nav_date": None,
        }
    else:
        payload = {
            "instrument_name": source_row.instrument_name,
            "share_class": source_row.share_class,
            "ticker_or_isin": source_row.ticker_or_isin,
            "management_firm_name": source_row.management_firm_name,
            "research_rating": source_row.research_rating,
            "research_rating_as_of": source_row.research_rating_as_of,
            "research_rating_updated_at": source_row.research_rating_updated_at,
            "return_ytd": source_row.return_ytd,
            "return_1w": source_row.return_1w,
            "return_mtd": source_row.return_mtd,
            "return_1m": source_row.return_1m,
            "return_1y": source_row.return_1y,
            "annualized_return": source_row.annualized_return,
            "return_3y": source_row.return_3y,
            "return_5y": source_row.return_5y,
            "max_drawdown": source_row.max_drawdown,
            "volatility": source_row.volatility,
            "sharpe_ratio": source_row.sharpe_ratio,
            "last_nav_date": source_row.last_nav_date,
        }

    payload["watchlist_id"] = watchlist_id
    payload["instrument_id"] = instrument_id
    payload["instrument_type"] = instrument_type
    payload["instrument_name"] = display_name or payload.get("instrument_name") or instrument_id.upper()
    payload["share_class"] = share_class or payload.get("share_class")
    payload["ticker_or_isin"] = ticker_or_isin or payload.get("ticker_or_isin")
    payload["management_firm_name"] = management_firm_name or payload.get("management_firm_name")
    # Research ratings are human judgements, not calculation outputs. Always
    # replace materialized values from the current rating revision so a market
    # data recalculation can never resurrect an older judgement.
    payload["research_rating"] = research_rating
    payload["research_rating_as_of"] = research_rating_as_of
    payload["research_rating_updated_at"] = research_rating_updated_at
    payload["attributes"] = attributes
    payload["data_freshness_status"] = freshness_status
    payload["market_data_input_watermark_at"] = market_data_input_watermark_at
    payload["last_recalculated_at"] = last_recalculated_at
    payload["last_successful_snapshot_at"] = last_successful_snapshot_at
    payload["staleness_reason"] = staleness_reason
    return payload


def _resolve_field_value(row: dict[str, object], field: str) -> object:
    if field.startswith("attr."):
        return row.get("attributes", {}).get(field.split(".", 1)[1])
    return row.get(field)


def _taxonomy_path_values(row: dict[str, object]) -> list[str]:
    values: list[str] = []
    for field in TAXONOMY_GROUP_FIELDS:
        value = _resolve_field_value(row, field)
        if value is None or value == "":
            break
        values.append(str(value))
    return values


def _taxonomy_group_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets: dict[str, dict[str, object]] = {}
    for row in rows:
        path_values = _taxonomy_path_values(row)
        if not path_values:
            bucket = buckets.setdefault(
                "Unspecified",
                {
                    "group_value": "Unspecified",
                    "row_count": 0,
                    "group_depth": 0,
                    "group_path": [],
                },
            )
            bucket["row_count"] = int(bucket["row_count"]) + 1
            continue
        for depth in range(1, len(path_values) + 1):
            path = path_values[:depth]
            group_value = " / ".join(path)
            bucket = buckets.setdefault(
                group_value,
                {
                    "group_value": group_value,
                    "row_count": 0,
                    "group_depth": depth - 1,
                    "group_path": path,
                },
            )
            bucket["row_count"] = int(bucket["row_count"]) + 1
    return sorted(
        buckets.values(),
        key=lambda item: (
            item["group_path"] == [],
            [str(value) for value in item.get("group_path", [])],
        ),
    )


def _normalize_string(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).replace("_", " ").lower().split())


def _matches_operator(left: object, operator: str, right: object) -> bool:
    if operator == "exists":
        return left is not None
    if operator == "not_in":
        values = right if isinstance(right, list) else [right]
        if left is None:
            return None not in values
        return all(_matches_operator(left, "neq", item) for item in values)
    if left is None:
        return False
    if operator == "eq":
        if isinstance(left, list):
            return any(_matches_operator(item, "eq", right) for item in left)
        if isinstance(left, str) or isinstance(right, str):
            return _normalize_string(left) == _normalize_string(right)
        return left == right
    if operator == "neq":
        if isinstance(left, list):
            return all(_matches_operator(item, "neq", right) for item in left)
        if isinstance(left, str) or isinstance(right, str):
            return _normalize_string(left) != _normalize_string(right)
        return left != right
    if operator == "contains":
        if isinstance(left, list):
            return right in left
        return _normalize_string(right) in _normalize_string(left)
    if operator == "in":
        values = right if isinstance(right, list) else [right]
        return any(_matches_operator(left, "eq", item) for item in values)
    if operator == "gte":
        return left >= right
    if operator == "lte":
        return left <= right
    if operator == "gt":
        return left > right
    if operator == "lt":
        return left < right
    return False


def _matches_advanced_filter(
    row: dict[str, object],
    node: dict[str, object] | None,
) -> bool:
    if not node:
        return True
    if node.get("type") == "rule":
        return _matches_operator(
            _resolve_field_value(row, str(node.get("field"))),
            str(node.get("operator")),
            node.get("value"),
        )
    logic = str(node.get("logic", "and")).lower()
    conditions = node.get("conditions", [])
    results = [
        _matches_advanced_filter(row, condition)
        for condition in conditions
        if isinstance(condition, dict)
    ]
    if not results:
        return True
    return any(results) if logic == "or" else all(results)


def _apply_filters(
    rows: list[dict[str, object]],
    filters: dict[str, list[object]],
) -> list[dict[str, object]]:
    filtered = rows
    for field, allowed_values in filters.items():
        if not allowed_values:
            continue
        filtered = [
            row
            for row in filtered
            if _resolve_field_value(row, field) is not None
            and any(
                _matches_operator(_resolve_field_value(row, field), "eq", value)
                for value in allowed_values
            )
        ]
    return filtered


def _apply_sort(
    rows: list[dict[str, object]],
    sort_rules: list[dict[str, str]],
) -> list[dict[str, object]]:
    sorted_rows = list(rows)
    for rule in reversed(sort_rules):
        field = rule.get("field")
        reverse = rule.get("direction", "asc").lower() == "desc"
        populated_rows: list[tuple[object, dict[str, object]]] = []
        empty_rows: list[dict[str, object]] = []
        for row in sorted_rows:
            value = _resolve_field_value(row, field)
            if value is None:
                empty_rows.append(row)
            else:
                populated_rows.append((value, row))
        populated_rows.sort(
            key=lambda item: item[0],
            reverse=reverse,
        )
        sorted_rows = [row for _, row in populated_rows] + empty_rows
    return sorted_rows


def _latest_datetime(
    rows: Sequence[dict[str, object]],
    *,
    keys: tuple[str, ...],
) -> datetime | None:
    candidates = []
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, datetime):
                candidates.append(value)
            elif isinstance(value, str) and value:
                try:
                    candidates.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
                except ValueError:
                    continue
    return max(candidates) if candidates else None


def _latest_date(rows: Sequence[dict[str, object]]) -> date | None:
    candidates: list[date] = []
    for row in rows:
        for key in ("latest_quote_date", "last_nav_date"):
            value = row.get(key)
            if isinstance(value, date):
                candidates.append(value)
                break
            if isinstance(value, str) and value:
                try:
                    candidates.append(date.fromisoformat(value[:10]))
                    break
                except ValueError:
                    continue
    return max(candidates) if candidates else None


def execute_watchlist_query(
    *,
    rows: Sequence[WatchlistRowReadModel],
    charts: Sequence[InstrumentChartReadModel] | None = None,
    payload: dict[str, object],
    view: WatchlistView | None,
) -> dict[str, object]:
    serialized_rows = [watchlist_row_to_dict(item) for item in rows]
    latest_quote_overrides = build_latest_quote_overrides(charts)
    if latest_quote_overrides:
        serialized_rows = [
            {
                **row,
                **latest_quote_overrides.get(str(row.get("instrument_id")), {}),
            }
            for row in serialized_rows
        ]

    filters_provided = "filters" in payload
    filters = payload.get("filters") or {}
    if not filters_provided and view is not None:
        filters = view.default_filters_json or {}

    advanced_filters_provided = "advanced_filters" in payload
    advanced_filters = payload.get("advanced_filters")
    if not advanced_filters_provided and view is not None:
        advanced_filters = view.default_advanced_filter_json or None

    sort_rules_provided = "sort" in payload
    sort_rules = payload.get("sort") or []
    if not sort_rules_provided and view is not None:
        sort_rules = view.default_sort_json or []

    group_by_provided = "group_by" in payload
    group_by = payload.get("group_by")
    if not group_by_provided and view is not None:
        group_by = view.default_group_by or "none"

    filtered_rows = _apply_filters(serialized_rows, filters)
    filtered_rows = [
        row
        for row in filtered_rows
        if _matches_advanced_filter(row, advanced_filters)
    ]
    filtered_rows = _apply_sort(filtered_rows, sort_rules)

    selected_fields_provided = "selected_fields" in payload
    selected_fields = list(payload.get("selected_fields") or [])
    if not selected_fields_provided and view is not None:
        selected_fields = [
            column.field_key
            for column in sorted(view.columns, key=lambda item: item.display_order)
            if column.is_visible
        ]
    if not selected_fields:
        selected_fields = ["instrument_name", "research_rating", "attr.fund_taxonomy_path"]
    if group_by == TAXONOMY_GROUP_BY_CODE:
        for field in TAXONOMY_GROUP_FIELDS:
            if field not in selected_fields:
                selected_fields.append(field)

    projected_rows = [
        {
            "instrument_id": row["instrument_id"],
            "instrument_type": row["instrument_type"],
            **{field: _resolve_field_value(row, field) for field in selected_fields},
        }
        for row in filtered_rows
    ]

    groups: list[dict[str, object]] = []
    if group_by == TAXONOMY_GROUP_BY_CODE:
        groups = _taxonomy_group_summary(filtered_rows)
    elif group_by and group_by != "none":
        buckets: dict[str, int] = {}
        for row in filtered_rows:
            bucket = str(_resolve_field_value(row, group_by) or "Unspecified")
            buckets[bucket] = buckets.get(bucket, 0) + 1
        groups = [
            {"group_value": key, "row_count": value}
            for key, value in sorted(buckets.items())
        ]

    pagination = payload.get("pagination", {}) or {}
    page = max(int(pagination.get("page", 1)), 1)
    page_size = max(int(pagination.get("page_size", 50)), 1)
    start = (page - 1) * page_size
    end = start + page_size

    stale_statuses = {"stale", "pending_recalc", "partial"}
    stale_row_count = sum(
        1 for row in filtered_rows if row.get("data_freshness_status") in stale_statuses
    )

    page_rows = projected_rows[start:end]
    page_instrument_ids = [str(row["instrument_id"]) for row in page_rows]

    return {
        "rows": page_rows,
        "groups": groups,
        "total_rows": len(projected_rows),
        "stale_row_count": stale_row_count,
        "sparklines": build_sparkline_payload(
            charts,
            instrument_ids=page_instrument_ids,
            selected_fields=selected_fields,
        ),
        "snapshot_metadata": {
            "as_of_date": _serialize_scalar(_latest_date(filtered_rows)),
            "methodology_version": "watchlist-row/v1",
            "market_data_input_watermark_at": _serialize_scalar(
                _latest_datetime(
                    filtered_rows,
                    keys=("market_data_input_watermark_at",),
                )
            ),
            "last_recalculated_at": _serialize_scalar(
                _latest_datetime(
                    filtered_rows,
                    keys=("last_recalculated_at",),
                )
            ),
            "is_current": True,
            "advanced_filter_applied": advanced_filters is not None,
        },
    }


def default_fund_summary_payload(
    instrument_id: str,
    instrument_attributes: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "fund_name": "Sample Fund",
        "ticker_or_isin": instrument_id.upper(),
        "management_firm_name": None,
        "research_rating": None,
        "instrument_attributes": instrument_attributes or {},
        "taxonomy": {
            "taxonomy_code": "fund_taxonomy",
            "assigned_node_id": None,
            "assigned_label": None,
            "path_labels": [],
            "path_node_ids": [],
            "depth": 0,
            "derived_values": {},
        },
        "key_stats": [],
        "freshness": {
            "data_freshness_status": "unavailable",
            "last_recalculated_at": None,
            "last_successful_snapshot_at": None,
            "market_data_input_watermark_at": None,
            "market_data_input_watermark_status": "unknown",
            "market_data_input_watermark_reason_code": (
                "no_read_model_materialized"
            ),
            "staleness_reason_codes": ["no_read_model_materialized"],
            "staleness_reason": "No read model materialized yet.",
        },
        "quick_monitoring_items": [],
        "tabs": ["overview"],
    }


def default_fund_chart_payload(instrument_id: str) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "base_series_type": "quote",
        "selected_series": {
            "role": None,
            "metric_family": None,
            "quote_basis": None,
            "series_type": None,
            "basis_type": None,
            "label": None,
            "date_label": None,
        },
        "currency": None,
        "date_range": None,
        "series": [],
        "available_compare_targets": [],
    }


def default_fund_performance_payload() -> dict[str, object]:
    return {
        "growth_chart_series": [],
        "annual_returns": [],
        "trailing_returns": [],
        "ranking": None,
        "peer_comparison": None,
        "calculation_frequency_profile": None,
        "snapshot_metadata": None,
    }


def default_fund_risk_payload() -> dict[str, object]:
    return {
        "risk_overview": None,
        "scatter_points": [],
        "risk_metrics": [],
        "drawdown_summary": None,
        "risk_structure": {"rows": []},
        "current_watch": {"overall_level": None, "rows": [], "note": None},
        "change_monitor": {"rows": [], "note": None},
        "calculation_frequency_profile": None,
        "snapshot_metadata": None,
    }


def merge_summary_attributes(
    payload: dict[str, object],
    attributes: dict[str, object],
) -> dict[str, object]:
    merged = dict(payload)
    merged["instrument_attributes"] = attributes
    return merged
