from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from watchlist_app.api.presenters import present_field_category, present_field_registry
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from watchlist_app.services.watchlist_query_contract import (
    WATCHLIST_COLUMN_FIELD_KEYS,
    WATCHLIST_FILTER_FIELD_KEYS,
)


router = APIRouter()
field_registry_repository = SQLAlchemyFieldRegistryRepository()


@router.get("")
def get_field_registry(
    category: str | None = Query(default=None),
    instrument_type: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    fields = [present_field_registry(item) for item in field_registry_repository.list_fields(session)]
    categories = [present_field_category(item) for item in field_registry_repository.list_categories(session)]
    if category:
        fields = [item for item in fields if item["category_code"] == category]
    if instrument_type:
        normalized_instrument_types = {
            value.strip().lower() for value in instrument_type.split(",") if value.strip()
        }
        if normalized_instrument_types:
            fields = [
                item
                for item in fields
                if not item["instrument_scope_json"]
                or normalized_instrument_types.issubset(
                    {
                        str(value).strip().lower()
                        for value in item["instrument_scope_json"]
                        if str(value).strip()
                    }
                )
            ]
    if product_type:
        fields = [
            item
            for item in fields
            if not item["product_scope_json"] or product_type in item["product_scope_json"]
        ]
    if search:
        needle = search.lower()
        fields = [
            item
            for item in fields
            if needle in item["label"].lower() or needle in item["field_key"].lower()
        ]
    available_field_keys = {str(item["field_key"]) for item in fields}
    return {
        "categories": categories,
        "fields": fields,
        "column_field_keys": [key for key in WATCHLIST_COLUMN_FIELD_KEYS if key in available_field_keys],
        "filter_field_keys": [key for key in WATCHLIST_FILTER_FIELD_KEYS if key in available_field_keys],
        "total_fields": len(fields),
    }
