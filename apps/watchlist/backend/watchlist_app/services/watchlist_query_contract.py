from __future__ import annotations

from datetime import date, datetime
from numbers import Real


SYSTEM_QUERY_FIELDS = {
    "instrument_id",
    "metric_as_of_date",
    "metric_return_kind",
    "metric_quote_basis",
    "metric_series_type",
}
ALLOWED_GROUP_BY_FIELDS = {
    "none",
    "instrument_type",
    "taxonomy",
    "data_freshness_status",
}
COMPARISON_OPERATORS = {"gte", "lte", "gt", "lt"}


class WatchlistQueryContractError(ValueError):
    pass


def _validate_filter_value(field, value: object, *, field_key: str) -> None:
    if value is None:
        return
    data_type = str(field.data_type).strip().lower()
    if data_type == "number":
        if isinstance(value, bool) or not isinstance(value, Real):
            raise WatchlistQueryContractError(f"Filter {field_key!r} requires a number.")
        return
    if data_type == "date":
        if not isinstance(value, str):
            raise WatchlistQueryContractError(f"Filter {field_key!r} requires an ISO date.")
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise WatchlistQueryContractError(
                f"Filter {field_key!r} requires an ISO date."
            ) from error
        return
    if data_type == "datetime":
        if not isinstance(value, str):
            raise WatchlistQueryContractError(
                f"Filter {field_key!r} requires an ISO datetime."
            )
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise WatchlistQueryContractError(
                f"Filter {field_key!r} requires an ISO datetime."
            ) from error
        return
    if data_type in {"string", "single_select", "sparkline"} and not isinstance(
        value, str
    ):
        raise WatchlistQueryContractError(f"Filter {field_key!r} requires text.")
    if data_type == "multi_select" and not isinstance(value, str):
        raise WatchlistQueryContractError(
            f"Filter {field_key!r} requires a selectable text value."
        )


def _require_query_field(fields: dict[str, object], field_key: str, *, operation: str):
    if field_key in SYSTEM_QUERY_FIELDS:
        return None
    field = fields.get(field_key)
    if field is None:
        raise WatchlistQueryContractError(f"Unknown {operation} field {field_key!r}.")
    return field


def _validate_advanced_filter(fields: dict[str, object], node: object) -> None:
    if not isinstance(node, dict):
        return
    if node.get("type") == "rule":
        field_key = str(node.get("field") or "").strip()
        field = _require_query_field(fields, field_key, operation="filter")
        if field is None or str(field.filter_mode) == "none":
            raise WatchlistQueryContractError(f"Field {field_key!r} is not filterable.")
        operator = str(node.get("operator") or "")
        if operator in COMPARISON_OPERATORS and str(field.data_type) not in {
            "number",
            "date",
            "datetime",
        }:
            raise WatchlistQueryContractError(
                f"Operator {operator!r} is not valid for field {field_key!r}."
            )
        if operator == "contains" and str(field.data_type) in {
            "number",
            "date",
            "datetime",
        }:
            raise WatchlistQueryContractError(
                f"Operator 'contains' is not valid for field {field_key!r}."
            )
        if operator == "exists":
            return
        value = node.get("value")
        if operator in {"in", "not_in"}:
            if not isinstance(value, list):
                raise WatchlistQueryContractError(
                    f"Operator {operator!r} requires a list."
                )
            for item in value:
                _validate_filter_value(field, item, field_key=field_key)
        else:
            _validate_filter_value(field, value, field_key=field_key)
        return
    for child in node.get("conditions") or []:
        _validate_advanced_filter(fields, child)


def validate_watchlist_query_contract(
    fields: dict[str, object],
    *,
    selected_fields: object,
    filters: object,
    sort_rules: object,
    group_by: object,
    advanced_filters: object,
) -> None:
    for field_key in selected_fields or []:
        _require_query_field(fields, str(field_key), operation="selected")
    for field_key, values in (filters or {}).items():
        field = _require_query_field(fields, str(field_key), operation="filter")
        if field is None or str(field.filter_mode) == "none":
            raise WatchlistQueryContractError(f"Field {field_key!r} is not filterable.")
        for value in values or []:
            _validate_filter_value(field, value, field_key=str(field_key))
    for rule in sort_rules or []:
        if not isinstance(rule, dict):
            raise WatchlistQueryContractError("Sort rules must be objects.")
        field_key = str(rule.get("field") or "")
        field = _require_query_field(fields, field_key, operation="sort")
        if field is None or str(field.sort_mode) == "none":
            raise WatchlistQueryContractError(f"Field {field_key!r} is not sortable.")
    if str(group_by or "none") not in ALLOWED_GROUP_BY_FIELDS:
        raise WatchlistQueryContractError(
            "Group By only supports instrument type, taxonomy, and data freshness."
        )
    _validate_advanced_filter(fields, advanced_filters)
